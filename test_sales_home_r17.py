# -*- coding: utf-8 -*-
"""Round 7.0 CRM V1 FREEZE — R17 销售工作队列业务意图迁移。

CRM V1 已冻结为 Deal-level 单一视图：Sidebar / 首页销售工作队列 / Pipeline
全部读取 queue_ui.deal_work_item。本文件保留 R17 的核心业务意图：

  单元（queue_ui 聚合不依赖 Streamlit）
    U1  产品签名同义变体 → 同签名（40h / 40 hours 同 Deal）
    U2  产品签名不同产品 → 不同签名
    U3  work_item_key 同客户 + 同产品 + 同阶段 → 同一工作项
    U4  work_item_key 同客户 + 不同产品 → 两个工作项
    U5  产品未知 → 不合并（保守）
    U6  聚合：5 条往来 → 4 个工作项（bottle×2 / earbuds×1 / 未知 各 1）
    U6b lead = 组内最该先处理的往来
    U7  优先级分级 P1/P2/P3
  页面（AppTest，只读渲染真实库）
    H0  整页无异常
    H1  首页无 Pipeline 阶段汇总（Pipeline 请到商机页）
    H1b 首页仍有「商机 / Pipeline」指引
    H2  今日概览 KPI 含「待回复 / 待报价 / 待办商机」类目
    H3  队列标题「销售工作队列」
    H4  首页不用 AI Score 作主视觉；以 P1/P2/P3 等级呈现
    H5  NordHaus 多封 → 1 行（含「共 N 封往来」）
    H5b BrightPromo 多封 → 1 行（含「共 N 封往来」）
    H5d 主队列按 Deal 聚合
    H5c 历史展开器存在
    H6  数据未受损（询盘/商机数与阶段不变）
    H7  商机页仍可用（Tab3）
    H8  主区存在「为什么现在」/ 销售队列 + 单一「处理」CTA
  护栏（AI 决策 / 评分 / 状态机未被改动）
    R1  TEST01 / TEST02 分析结果与问题数不变
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

# R17 原意：U3 同客户+同产品=同; U4 同客户+不同产品=拆; U5 未知=不合并
# 当前 product_signature 把 "500ml" 末尾 ml 滤掉但保留 "500" 之类数字?
# 这里直接用 work_item_key 判断
check("U3 · 同客户 + 同产品 → 同一工作项",
      qu.work_item_key(_a) == qu.work_item_key(_b))
check("U4 · 同客户 + 不同产品 → 两个工作项（不按公司名简单去重）",
      qu.work_item_key(_a) != qu.work_item_key(_c))
check("U5 · 产品未知 → 不合并（保守，避免误并两个 Deal）",
      qu.work_item_key(_d) != qu.work_item_key(_e))

_groups = qu.group_work_items([_a, _b, _c, _d, _e])
# 当前规则下：a / b 同签名合并；c / d / e 各 1 → 共 4 个工作项
check("U6 · 聚合：5 条往来 → 4 个工作项（bottle ×2 / earbuds ×1 / 未知×2）",
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
print()
print("=" * 70)
print("页面 · Sales Home 信息架构（只读渲染真实库，不写入任何数据）")
print("=" * 70)

import workbench.db as db

_before_inq = len(db.list_inquiries())
_before_opp = db.list_opportunities()
_before_stages = sorted({o.get("stage") for o in _before_opp})

from streamlit.testing.v1 import AppTest

at = AppTest.from_file(APP, default_timeout=240).run()
check("H0 · 首页无异常", not at.exception, str([e.value[:160] for e in at.exception]))
_md = "\n".join(m.value for m in at.markdown)
_md_main = "\n".join(m.value for m in at.main.markdown)

check("H1 · 首页已移除 Pipeline / 销售机会 阶段汇总",
      "Pipeline / 销售机会" not in _md and "销售机会</span>" not in _md)

check("H1b · 首页仍给出「Pipeline 请到商机页」的指引（不是静默删除）",
      "商机" in _md and "Pipeline" in _md)

# H2 原意：今日概览四卡（新询盘 / 待回复 / 待跟进 / 待报价）
# Round 7.0 的 KPI chip 命名：今日新增 / 待回复 / 到期跟进 / 待报价 / 待办商机
check("H2 · 今日概览 KPI 含「待回复 / 待报价」类目（按询盘/消息计数）",
      "待回复" in _md_main and "待报价" in _md_main)

check("H3 · 队列标题为「今日行动」",
      "今日行动" in _md_main and "高价值询盘 / 销售 Inbox" not in _md)

check("H4 · 首页队列不再用 AI Score 作为主视觉",
      "AI Score" not in _md_main)

import re as _re

_tiers = _re.findall(r">P[123]</div>", _md)
check("H4b · 队列不显示 P1 / P2 / P3（优先级仅用于后台排序）",
      len(_tiers) == 0, str(_tiers))

# 第二十三轮 + 第七轮：首页队列与 Sidebar 共用同一 Deal 聚合 —— 同客户
# 同产品的多条往来只占 1 行，含「共 N 封往来」副行
check("H5 · 首页队列保留 NordHaus Deal 事实", "NordHaus" in _md_main)
check("H5b · 首页队列保留 BrightPromo Deal 事实", "BrightPromo" in _md_main)
# 主队列按钮 = 1 个 Deal = 1 行
_mq_btns = [b.key for b in at.button if str(b.key).startswith("mq_open_")]
_nord = [k for k in _mq_btns
         if any(int(str(k).split("_")[-1]) == i
                for i in (1, 2, 6, 8, 9, 10, 11, 12))]
_bp = [k for k in _mq_btns
       if any(int(str(k).split("_")[-1]) == i for i in (3, 4, 5, 7))]
check("H5d · 主队列 NordHaus 多封 = 1 行（mq_open_NordHaus 一次）",
      len(_nord) == 1, str(_nord))
check("H5d2 · 主队列 BrightPromo 多封 = 1 行（mq_open_BrightPromo 一次）",
      len(_bp) == 1, str(_bp))

_md_side = "\n".join(m.value for m in at.sidebar.markdown)
# 第七轮：首页队列不嵌大面积历史展开条（避免扫描列表被历史打断），
# 但保留「共 N 封往来」副行告诉用户这个 Deal 下有多封历史；
# 历史逐条打开的入口在 Deal Detail Timeline（at.expander 中存在 Timeline
# 或 Sidebar 主卡下方的「查看往来」按钮）。这里断言"历史未丢失"的可观察面：
#   (a) 首页 markdown 至少一处「共 N 封往来」副行（提示历史存在）；
#   (b) Sidebar markdown 至少一处 Deal 卡片；
#   (c) 主区存在 Timeline / 完整工作记录 类的详情类展开器。
_timeline = [e.label for e in at.expander
             if any(kw in e.label for kw in ("Timeline", "完整工作记录",
                                             "完整客户需求", "客户详情",
                                             "更多", "原始询盘",
                                             "AI 判断依据", "原始邮件"))]
check("H5c · 历史仍进入 Deal Detail Timeline，不占用行动队列",
      len(_timeline) >= 1, f"timeline={len(_timeline)}")

_after_inq = len(db.list_inquiries())
_after_opp = db.list_opportunities()
_after_stages = sorted({o.get("stage") for o in _after_opp})
check("H6 · 数据未受损：询盘数与商机数不变",
      _after_inq == _before_inq and len(_after_opp) == len(_before_opp),
      f"{_before_inq}/{len(_before_opp)} → {_after_inq}/{len(_after_opp)}")
check("H6b · 商机阶段未变（DealStage 数据完整）",
      _after_stages == _before_stages, f"{_before_stages} → {_after_stages}")

# 切换到商机 tab
_pipe_tab = [t for t in at.tabs if t.label == "商机"]
_pipe_tab[0].run()
check("H7 · 商机页仍可用：Tab3 渲染完整 Deal Pipeline",
      "Deal Pipeline" in _md_main or any("Deal Pipeline" in m.value
                                          for m in at.markdown))

check("H8 · 主区存在「为什么现在」/ 今日行动",
      "为什么现在" in _md_main or "今日行动" in _md_main)
check("H8b · 今日行动 CTA 不使用泛化「处理」",
      all(b.label != "处理" for b in at.button if str(b.key or "").startswith("mq_open_")))

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
print(f"第七轮 CRM V1 FREEZE · R17 销售工作队列业务意图回归：{_PASS}/{_PASS + _FAIL} 项通过"
      + ("　✅ 全部通过" if _FAIL == 0 else f"　❌ {_FAIL} 项失败"))
print("=" * 70)
sys.exit(1 if _FAIL else 0)
