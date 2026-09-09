# -*- coding: utf-8 -*-
"""Phase 3（第十三轮）· AI Priority + Next Best Action 验收测试

对应用户规格七：
  Case 1  高采购意向 + 产品明确            → 高 Priority
  Case 2  只有一句简单询价                  → 不应该虚高
  Case 3  缺产品信息                        → Quote Readiness = NOT_READY
  Case 4  客户超过 48 小时未跟进            → Follow-up Risk 增加
  Case 5  数据缺失                          → 不得随机评分（无依据分禁止）
  Case 6  报价完成                          → Next Action 改变
追加断言（规格一/三/四/五/六）：
  总分 = Σ(维度分×权重)，可复算、无随机源、0-100 越界防护
  七维 nodata 一律「暂无数据」、不得出现无依据的具体分数
  8 类 Action Type 均可由 workflow 动作映射到
  Queue Score 综合 Priority/Urgency/Overdue/Stage：终态沉底、逾期置顶
  异常情况：坏时间/缺字段/空输入不抛异常
纯函数测试，不连库、不写任何业务数据。
"""
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench"))

from workbench import priority3 as p3
from workbench import workflow as wf

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {name}")
    else:
        _FAIL += 1
        print(f"  ❌ {name}  {detail}")


def ts(hours_ago: float) -> str:
    return (datetime.datetime.now()
            - datetime.timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M")


def mkitem(**kw):
    """构造 analyze 的 item。缺省为「一句话泛询价、刚入库、无产品信息」样本。"""
    base = {
        "id": 1, "created": ts(0.5), "status": "待处理", "biz": "READY_TO_REPLY",
        "need": {
            "intent": "send me your price",
            "qty": "", "product": "", "product_cat": "", "match_top": {},
            "readiness": "insufficient_info", "blockers": [],
            "qty_num": None, "price_num": None,
            "lead_summary": {
                "intent": None, "volume": None, "clarity": None,
                "quality": None, "match": None, "conversion": None,
                "maturity": None, "urgency": "low", "urgency_score": 15,
                "overall": None,
            },
        },
        "wf": {"last_replied_at": "", "follow_up_at": "",
               "follow_up_done": 0, "deal_status": ""},
        "matches": [], "lead": {},
    }
    for k, v in kw.items():
        base[k] = v
    return base


def mkact(**kw):
    a = {"type": "SEND_REPLY", "label": "回复客户", "priority": "P2"}
    a.update(kw)
    return a


# ================= Case 1：高采购意向 + 产品明确 → 高 Priority =================
print("== Case 1：高采购意向 + 产品明确 ==")
c1 = mkitem(
    biz="READY_FOR_QUOTE",
    need={"intent": "we need 10000 pcs swim caps urgently",
          "qty": "10,000 pcs", "product": "成人硅胶泳帽", "product_cat": "泳帽",
          "match_top": {"name": "成人硅胶泳帽", "score": 0.92},
          "readiness": "ready_for_quotation", "blockers": [],
          "qty_num": 10000, "price_num": None,
          "lead_summary": {"intent": 100, "volume": 90, "clarity": 85,
                           "quality": 80, "match": 92, "conversion": 88,
                           "maturity": 70, "urgency": "high",
                           "urgency_score": 90, "overall": 88}},
    wf={"last_replied_at": "", "follow_up_at": "", "follow_up_done": 0,
        "deal_status": ""})
r1 = p3.analyze(c1, mkact(type="CREATE_QUOTE", label="生成报价", priority="P1"))
check("高意向+产品明确 → AI Priority ≥ 75（实际 %d）" % r1["aip"],
      r1["aip"] >= 75, str(r1["aip"]))
check("产品匹配已命中（非暂无数据）",
      next(x for x in r1["rows"] if x["key"] == "product_match")["state"]
      == "matched")
check("报价准备度 READY_FOR_QUOTE",
      "READY_FOR_QUOTE" in next(x for x in r1["rows"]
                                if x["key"] == "quote_readiness")["display"])
check("Next Action = 创建报价（CREATE_QUOTE）",
      r1["nba"]["action_type"] == "CREATE_QUOTE", r1["nba"]["action_type"])

# ================= Case 2：只有一句简单询价 → 不虚高 =================
print("== Case 2：一句话简单询价 ==")
c2 = mkitem()
r2 = p3.analyze(c2, mkact())
check("简单询价 AI Priority ≤ 40（实际 %d，绝不虚高）" % r2["aip"],
      r2["aip"] <= 40, str(r2["aip"]))
check("简单询价不是高优先档",
      r2["level_cn"].startswith(("⚪", "🟢")), r2["level_cn"])
check("缺数据维以「暂无数据」呈现（不编 83 这类分数）",
      all(x["state"] != "nodata" or x["display"] == "暂无数据"
          for x in r2["rows"]))

# ================= Case 3：缺产品信息 → Quote Readiness NOT_READY =================
print("== Case 3：缺产品信息 ==")
c3 = mkitem(
    biz="NEEDS_INFO",
    need={"intent": "interested in caps", "qty": "", "product": "",
          "product_cat": "", "match_top": {},
          "readiness": "insufficient_info",
          "blockers": ["product", "quantity"], "qty_num": None,
          "price_num": None,
          "lead_summary": {"intent": 60, "volume": 10, "clarity": 20,
                           "quality": 20, "match": 5, "conversion": 10,
                           "maturity": 10, "urgency": "low",
                           "urgency_score": 15, "overall": 25}})
r3 = p3.analyze(c3, mkact(type="CONFIRM_PRODUCT", label="确认产品",
                          priority="P0"))
qr3 = next(x for x in r3["rows"] if x["key"] == "quote_readiness")
check("缺产品信息 → 报价准备度显示 NOT_READY（枚举，不虚报分数）",
      qr3["display"].startswith("NOT_READY"), qr3["display"])
check("产品匹配维：知道要什么但库未命中 → 不显示 83",
      next(x for x in r3["rows"] if x["key"] == "product_match")["score"] <= 20)
check("Next Action = 收集信息 COLLECT_INFO（先问清产品/数量）",
      r3["nba"]["action_type"] == "COLLECT_INFO", r3["nba"]["action_type"])
check("NBA reason 引用缺失字段",
      "产品" in r3["nba"]["reason"] and "数量" in r3["nba"]["reason"],
      r3["nba"]["reason"])

# ================= Case 4：客户超过 48h 未跟进 → Follow-up Risk 增加 =================
print("== Case 4：超 48 小时未跟进 ==")
c4a = mkitem(created=ts(2))       # 2 小时前到
c4b = mkitem(created=ts(60))      # 60 小时前到
ra = p3.analyze(c4a, mkact())
rb = p3.analyze(c4b, mkact())
risk_a = next(x for x in ra["rows"] if x["key"] == "followup_risk")
risk_b = next(x for x in rb["rows"] if x["key"] == "followup_risk")
check("2h 未跟进风险低 vs 60h 未跟进风险显著更高（%s < %s）"
      % (risk_a["score"], risk_b["score"]),
      risk_b["score"] > risk_a["score"] and risk_b["score"] >= 60,
      f"{risk_a['score']} vs {risk_b['score']}")
check("60h 未跟进理由点明超 48 小时",
      "48 小时" in risk_b["reason"], risk_b["reason"])
check("超时询盘 Queue Score 上升（Overdue 综合进队列）",
      rb["qs"] > ra["qs"], f"{ra['qs']} vs {rb['qs']}")

# ================= Case 5：数据缺失 → 不得随机评分 =================
print("== Case 5：数据缺失不随机 ==")
c5 = mkitem()
x1 = p3.analyze(c5, mkact())
x2 = p3.analyze(c5, mkact())
check("同一输入两次结果完全一致（无随机源）",
      x1["aip"] == x2["aip"]
      and [r["score"] for r in x1["rows"]] == [r["score"] for r in x2["rows"]])
nodata_keys = [r["key"] for r in x1["rows"] if r["state"] == "nodata"]
check("缺数据维数 ≥ 4（intent/clarity/match/value 无依据；"
      "报价准备度恒有 insufficient 判定）",
      len(nodata_keys) >= 4, str(nodata_keys))
check("nodata 维 0 分且显示「暂无数据」",
      all(r["display"] == "暂无数据" and r["score"] == 0
          for r in x1["rows"] if r["state"] == "nodata"))

# ================= Case 6：报价完成 → Next Action 改变 =================
print("== Case 6：报价完成前后 Next Action 改变 ==")
c6a = mkitem(
    biz="READY_FOR_QUOTE",
    need={"readiness": "ready_for_quotation", "blockers": [],
          "product": "泳帽", "qty": "1000 pcs", "qty_num": 1000,
          "lead_summary": {"intent": 90, "volume": 70, "clarity": 70,
                           "quality": 60, "match": 80, "conversion": 75,
                           "maturity": 50, "urgency": "medium",
                           "urgency_score": 55, "overall": 70}},
    wf={"last_replied_at": "", "follow_up_at": "", "follow_up_done": 0,
        "deal_status": ""})
r6a = p3.analyze(c6a, mkact(type="CREATE_QUOTE", label="生成报价",
                            priority="P1"))
c6b = mkitem(
    biz="QUOTED",
    need={"readiness": "quoted", "blockers": [], "product": "泳帽",
          "qty": "1000 pcs", "qty_num": 1000,
          "lead_summary": {"intent": 90, "volume": 70, "clarity": 70,
                           "quality": 60, "match": 80, "conversion": 75,
                           "maturity": 50, "urgency": "medium",
                           "urgency_score": 55, "overall": 70}},
    wf={"last_replied_at": ts(20), "follow_up_at": "", "follow_up_done": 0,
        "deal_status": ""})
r6b = p3.analyze(c6b, mkact(type="FOLLOW_UP_QUOTE", label="跟进报价反馈",
                            priority="P1"))
check("报价前 Next Action = 生成报价",
      r6a["nba"]["action_type"] == "CREATE_QUOTE"
      and "报价" in r6a["nba"]["action_label"], r6a["nba"]["action_type"])
check("报价后 Next Action 改变为跟进报价（FOLLOW_UP）",
      r6b["nba"]["action_type"] == "FOLLOW_UP"
      and "跟进" in r6b["nba"]["action_label"],
      r6b["nba"]["action_type"] + " / " + r6b["nba"]["action_label"])
check("报价完成 → 报价准备度显示 QUOTED",
      "QUOTED" in next(x for x in r6b["rows"]
                       if x["key"] == "quote_readiness")["display"])

# ================= 追加一：总分可复算 + 权重和 = 1 =================
print("== 公式可复算 ==")
check("七维权重和 = 1.00",
      abs(sum(p3.AIP_WEIGHTS.values()) - 1.0) < 1e-9,
      str(sum(p3.AIP_WEIGHTS.values())))
_cases = [c1, c2, c3, c5, c6a, c6b]
for _i, _c in enumerate(_cases):
    _r = p3.analyze(_c, mkact())
    _manual = round(sum(d["score"] * p3.AIP_WEIGHTS[d["key"]]
                        for d in _r["rows"]))
    check(f"样本{_i+1}：AI Priority == Σ(维度分×权重)（{_r['aip']} == {_manual}）",
          _r["aip"] == _manual and 0 <= _r["aip"] <= 100,
          f"{_r['aip']} vs {_manual}")
    check(f"样本{_i+1}：无随机源（可复现）",
          _r["aip"] == p3.analyze(_c, mkact())["aip"])

# ================= 追加二：8 类 Action Type 映射覆盖 =================
print("== Action Type 八类 ==")
_act_tests = [
    ("REVIEW_REPLY", "查看并发送回复", "REPLY"),
    ("SEND_REPLY", "回复客户", "REPLY"),
    ("CONFIRM_PRODUCT", "确认产品", "COLLECT_INFO"),
    ("CHECK_SAMPLE", "确认样品", "COLLECT_INFO"),
    ("CREATE_QUOTE", "生成报价", "CREATE_QUOTE"),
    ("FOLLOW_UP_CUSTOMER", "立即跟进", "FOLLOW_UP"),
    ("CREATE_FOLLOW_UP", "设置跟进时间", "SCHEDULE_FOLLOW_UP"),
]
for _t, _lb, _exp in _act_tests:
    _r = p3.analyze(mkitem(), mkact(type=_t, label=_lb))
    check(f"{_t} → {_exp}", _r["nba"]["action_type"] == _exp,
          _r["nba"]["action_type"])
_w = p3.analyze(mkitem(biz="WON"), mkact(type=None, label="已成交"))
check("WON → MARK_COMPLETE", _w["nba"]["action_type"] == "MARK_COMPLETE",
      _w["nba"]["action_type"])
_n = p3.analyze(mkitem(biz="NEGOTIATING"),
                mkact(type="FOLLOW_UP_QUOTE", label="跟进报价反馈"))
check("NEGOTIATING → NEGOTIATE", _n["nba"]["action_type"] == "NEGOTIATE",
      _n["nba"]["action_type"])
_l = p3.analyze(mkitem(biz="LOST"), mkact(type=None, label="已丢单"))
check("LOST → MARK_COMPLETE", _l["nba"]["action_type"] == "MARK_COMPLETE",
      _l["nba"]["action_type"])

# ================= 追加三：Queue Score 综合排序 =================
print("== Queue Score ==")
# 高 AIP、老单、刚入、终态、历史已处理
_it_now = mkitem(id=10, created=ts(1),
                 need={"readiness": "ready_for_quotation", "blockers": [],
                       "product": "泳帽", "qty_num": 5000,
                       "lead_summary": {"intent": 95, "volume": 90,
                                        "clarity": 85, "quality": 70,
                                        "match": 90, "conversion": 85,
                                        "maturity": 60, "urgency": "high",
                                        "urgency_score": 90, "overall": 90}})
_it_old = mkitem(id=11, created=ts(60))          # 简单老询盘（逾期）
_it_won = mkitem(id=12, biz="WON", created=ts(10),
                 wf={"last_replied_at": "", "follow_up_at": "",
                     "follow_up_done": 0, "deal_status": "WON"})
_a = p3.analyze(_it_now, mkact(type="CREATE_QUOTE", label="生成报价"))
_b = p3.analyze(_it_old, mkact())
_c = p3.analyze(_it_won, mkact(type=None, label="已成交"))
check("高意向新单 Queue 分最高（%d）" % _a["qs"],
      _a["qs"] > _b["qs"] and _a["qs"] > _c["qs"],
      f"{_a['qs']} {_b['qs']} {_c['qs']}")
check("WON 沉底（Queue 分最低）", _c["qs"] < _b["qs"],
      f"{_c['qs']} vs {_b['qs']}")
check("QS 综合了 stage（阶段紧急度）",
      _a["qs_parts"]["stage"] > 0 and _c["qs_parts"]["stage"] == 0,
      str(_a["qs_parts"]))

# ================= 追加四：异常情况 =================
print("== 异常情况 ==")
try:
    p3.analyze({}, {})
    check("空输入不抛异常", True)
except Exception as e:                                  # noqa
    check("空输入不抛异常", False, str(e))
try:
    p3.analyze(mkitem(created="bad-time"), mkact())
    check("坏时间字符串不抛异常（按暂无时间处理）", True)
except Exception as e:                                  # noqa
    check("坏时间字符串不抛异常", False, str(e))
try:
    p3.analyze(mkitem(need=None), mkact())
    check("need=None 不抛异常", True)
except Exception as e:                                  # noqa
    check("need=None 不抛异常", False, str(e))
try:
    _unk = mkitem()
    _unk["need"] = dict(_unk["need"], readiness="weird_unknown_status")
    _p = p3.ai_priority(_unk)
    check("readiness 未知值 → nodata 不猜",
          next(x for x in _p["rows"]
               if x["key"] == "quote_readiness")["state"] == "nodata", "")
except Exception as e:                                  # noqa
    check("readiness 未知值处理", False, str(e))

print()
print(f"结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
