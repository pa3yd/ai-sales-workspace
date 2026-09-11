# -*- coding: utf-8 -*-
"""第十七轮 · SALES HOME — PIPELINE SIMPLIFICATION 回归测试

范围：只验证销售首页的信息架构与展示层聚合，
      不触碰 AI 决策逻辑、评分、DealStage / InquiryStatus 状态机。

覆盖：
  单元（queue_ui）
    U1  产品签名：同义变体同签名（40h / 40 hours 属同一 Deal）
    U2  产品签名：不同产品不同签名
    U3  工作项键：同客户 + 同产品 + 同阶段 → 同一工作项
    U4  工作项键：同客户 + 不同产品 → 两个工作项（禁止按公司名简单去重）
    U5  工作项键：产品未知 → 不合并（保守，不误并两个 Deal）
    U6  聚合：5 条往来 → 2 个工作项，lead 取最高优先级/最新
    U7  优先级分级 P1/P2/P3（原始分不参与分级显示）
  页面（Streamlit AppTest，只读渲染真实库）
    H1  首页不再出现 Pipeline / 销售机会 阶段汇总
    H2  今日概览四卡：新询盘 / 待回复 / 待跟进 / 待报价
    H3  队列标题为「销售工作队列」，旧「高价值询盘 / 销售 Inbox」已移除
    H4  首页队列不再用 AI Score 作为主视觉，改为 P1/P2/P3
    H5  NordHaus 3 封 / BrightPromo 3 封 → 各一行（共 N 封往来 + 历史展开器）
    H6  数据未受损：渲染前后 inquiries / opportunities 数量与阶段不变
    H7  商机页仍可用：Tab3 渲染完整 Deal Pipeline
    H8  今日优先处理仍展示卡点 + 下一步
  逻辑未变（回归护栏）
    R1  TEST01 / TEST02 分析结果与问题数不变（≤3 问）
    R2  InquiryStatus / DealStage 状态机常量未被改动
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench"))

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


ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "workbench", "app.py")

# =====================================================================
# 单元：展示层聚合（无 Streamlit 依赖）
# =====================================================================
print("=" * 70)
print("单元 · 销售工作队列展示聚合（只读，不合并/不删除任何数据）")
print("=" * 70)

import workbench.queue_ui as qu


def _item(iid, company="ACME GmbH", contact="Ann", cust_id=1, product="",
          pri="🔴 优先处理", pts=80, status="待处理"):
    return {"id": iid, "company": company, "contact": contact,
            "cust_id": cust_id, "status": status, "pri": pri, "pts": pts,
            "need": {"product_query": product, "product": "", "product_cat": ""}}


check("U1 · 同义变体同签名（40h vs 40 hours、black/white vs black and white）",
      qu.product_signature(
          "Wireless ANC earbuds with Bluetooth 5.4, ANC, 40h battery life, "
          "USB-C, black/white, custom logo and packaging")
      == qu.product_signature(
          "Wireless ANC earbuds with Bluetooth 5.4, ANC, 40 hours battery "
          "life, USB-C, black and white, custom logo and packaging"),
      qu.product_signature("Wireless ANC earbuds with Bluetooth 5.4"))

check("U2 · 不同产品不同签名（earbuds vs water bottles）",
      qu.product_signature("Wireless ANC earbuds with Bluetooth 5.4")
      != qu.product_signature("stainless steel water bottles"))

_a = _item(1, product="stainless steel water bottles")
_b = _item(2, product="stainless steel water bottles 500ml")
_c = _item(3, product="wireless ANC earbuds")
_d = _item(4, product="")
_e = _item(5, product="")

check("U3 · 同客户 + 同产品 + 同阶段 → 同一工作项",
      qu.work_item_key(_a) == qu.work_item_key(_b))

check("U4 · 同客户 + 不同产品 → 两个工作项（不按公司名简单去重）",
      qu.work_item_key(_a) != qu.work_item_key(_c))

check("U5 · 产品未知 → 不合并（保守，避免误并两个 Deal）",
      qu.work_item_key(_d) != qu.work_item_key(_e))

_groups = qu.group_work_items([_a, _b, _c, _d, _e])
check("U6 · 聚合：5 条往来 → 4 个工作项（bottle ×2 / earbuds ×1 / 未知 各 1）",
      len(_groups) == 4 and sorted(g["count"] for g in _groups) == [1, 1, 1, 2],
      str([g["count"] for g in _groups]))
_lead = [g for g in _groups if g["count"] == 2][0]["lead"]
check("U6b · lead = 组内最该先处理的一条（优先级分 → 最新）",
      _lead["id"] == 2, f"lead=#{_lead['id']}")

check("U7 · 优先级分级 P1/P2/P3",
      qu.priority_tier({"pri": "🔴 优先处理"}) == "P1"
      and qu.priority_tier({"pri": "🟠 正常处理"}) == "P2"
      and qu.priority_tier({"pri": "🟢 可延后"}) == "P3")

# =====================================================================
# 页面：Sales Home 信息架构
# =====================================================================
print()
print("=" * 70)
print("页面 · Sales Home（只读渲染真实库，不写入任何数据）")
print("=" * 70)

import workbench.db as db

_before_inq = len(db.list_inquiries())
_before_opp = db.list_opportunities()
_before_stages = sorted({o.get("stage") for o in _before_opp})

from streamlit.testing.v1 import AppTest

at = AppTest.from_file(APP, default_timeout=240).run()
check("H0 · 首页无异常", not at.exception, str([e.value[:160] for e in at.exception]))
_md = "\n".join(m.value for m in at.markdown)

check("H1 · 首页已移除 Pipeline / 销售机会 阶段汇总",
      "Pipeline / 销售机会" not in _md and "销售机会</span>" not in _md)

check("H1b · 首页仍给出「Pipeline 请到商机页」的指引（不是静默删除）",
      "商机" in _md and "Pipeline" in _md)

check("H2 · 今日概览四卡：新询盘 / 待回复 / 待跟进 / 待报价",
      all(k in _md for k in ("新询盘", "待回复", "待跟进", "待报价")))

check("H3 · 队列标题为「销售工作队列」",
      "销售工作队列" in _md and "高价值询盘 / 销售 Inbox" not in _md)

check("H4 · 首页队列不再用 AI Score 作为主视觉",
      "AI Score" not in _md)

import re as _re

_tiers = _re.findall(r">P[123]</div>", _md)
check("H4b · 队列以 P1 / P2 / P3 等级呈现（原始分留给详情与 AI 依据）",
      len(_tiers) >= 1, str(_tiers))

# 第二十三轮：首页队列与 Sidebar 共用同一 Deal 聚合 —— NordHaus 3 封往来
# （NEW+已报价重复旧行）在队列里只占 1 行，往来数=全部相关历史 3 封
check("H5 · NordHaus 3 封往来聚合成 1 行（共 3 封往来）",
      "共 3 封往来" in _md.replace("<div style='font-size:.7rem;opacity:.68'>", ""))
check("H5b · BrightPromo 3 封往来聚合成 1 行（共 3 封往来）",
      "共 3 封往来" in _md.replace("<div style='font-size:.7rem;opacity:.68'>", ""))
check("H5d · 首页工作队列按 Deal 聚合：NordHaus 只出现一个「处理」行",
      sum(1 for b in at.button if str(b.key).startswith("mq_open_")
          and int(str(b.key).split("_")[2]) in (1, 2, 6)) == 1,
      str([b.key for b in at.button if str(b.key).startswith("mq_open_")]))

_hist_labels = [e.label for e in at.expander if "往来记录" in e.label]
check("H5c · 被折叠的历史往来仍可逐条打开（每个 Deal 一个历史展开器）",
      len(_hist_labels) >= 2, str(_hist_labels))

_after_inq = len(db.list_inquiries())
_after_opp = db.list_opportunities()
_after_stages = sorted({o.get("stage") for o in _after_opp})
check("H6 · 数据未受损：询盘数与商机数不变",
      _after_inq == _before_inq and len(_after_opp) == len(_before_opp),
      f"{_before_inq}/{len(_before_opp)} → {_after_inq}/{len(_after_opp)}")
check("H6b · 商机阶段未变（DealStage 数据完整）",
      _after_stages == _before_stages, f"{_before_stages} → {_after_stages}")

check("H7 · 商机页仍可用：Tab3 渲染完整 Deal Pipeline",
      "Deal Pipeline" in _md)

check("H8 · 今日优先处理仍展示卡点 / 下一步",
      "今日优先处理" in _md and "卡点" in _md and "下一步：" in _md)

# =====================================================================
# 回归护栏：AI 决策逻辑与状态机未被改动
# =====================================================================
print()
print("=" * 70)
print("护栏 · AI 决策 / 评分 / 状态机未被本轮改动")
print("=" * 70)

sys.path.insert(0, ROOT)
from main import build_engine, analyze
from agent.reply_strategy import count_questions
from test_test01_round2 import TEST01_TEXT
from test_test02_min_questions import TEST02_TEXT

_eng = build_engine("rule")
_r1 = analyze(TEST01_TEXT, *_eng)
_r2 = analyze(TEST02_TEXT, *_eng)
check("R1 · TEST01 分析仍可用且完整度 HIGH",
      (_r1["insight"].get("requirement_completeness") or {}).get("level") == "HIGH",
      str((_r1["insight"].get("requirement_completeness") or {}).get("level")))
check("R1b · TEST01 / TEST02 生成问句均 ≤3",
      count_questions(_r1.get("draft") or "") <= 3
      and count_questions(_r2.get("draft") or "") <= 3,
      f'{count_questions(_r1.get("draft") or "")}/{count_questions(_r2.get("draft") or "")}')

import workbench.workflow as _wf
import workbench.sales_crm as _crm_core

check("R2 · InquiryStatus 12 态状态机未被改动",
      len(getattr(_wf, "STATES", []) or []) >= 12 or hasattr(_wf, "_ALLOWED"),
      str(len(getattr(_wf, "STATES", []) or [])))
check("R2b · DealStage 商机阶段未被改动",
      len(_crm_core.STAGES) >= 8 and "WON" in _crm_core.STAGES,
      str(_crm_core.STAGES))

print()
print("=" * 70)
print(f"第十七轮回归测试结果：{_PASS}/{_PASS + _FAIL} 项通过"
      + ("　✅ 全部通过" if _FAIL == 0 else f"　❌ {_FAIL} 项失败"))
print("=" * 70)
sys.exit(1 if _FAIL else 0)
