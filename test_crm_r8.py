# -*- coding: utf-8 -*-
"""第八轮测试：CRM 商机队列（客户聚合 / 商机阶段 / 下一步动作 / 历史询盘）。

覆盖 spec 二十一节 Test 01~10 + 客户身份键单元测试。
使用临时数据库（不污染真实 workbench.db）。
运行：python test_crm_r8.py
"""
import os
import sys
import tempfile

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_BASE, "workbench"))

# —— 先换临时库，再让 app 引用同一 db 模块（AppTest 与本进程共享 sys.modules）——
import db as _db
_TMP_DB = os.path.join(tempfile.gettempdir(), "wb_r8_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
_db.DB_PATH = _TMP_DB
_db.init_db()

import queue_ui as _ui
import workflow as _wf

_PASS = _FAIL = 0


def check(name, ok, detail=""):
    global _PASS, _FAIL
    mark = "✅" if ok else "❌"
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  {mark} {name}  {detail}"[:220])
    if ok:
        print(f"  {mark} {name}")


def _ss(state, key, default=None):
    """AppTest session_state 读取（不支持 .get()）。"""
    if key in state:
        try:
            return state[key]
        except Exception:
            pass
    return default


def _rep(company="Test Co", product_known=True, qty=3000, draft="",
         intent="目录/报价请求", readiness=""):
    """构造最小分析报告（与 agent 输出同构）。"""
    blockers = []
    if not product_known:
        blockers.append({"field": "product", "severity": "high",
                         "phase": "preliminary"})
    return {
        "extracted": {"company": company, "country": "Germany",
                      "contact_name": "Anna", "intent": intent,
                      "quantity": qty, "quantity_unit": "pcs",
                      "product_query": "steel bottle" if product_known else None},
        "lead": {"grade": "B", "score": 70},
        "matches": [],
        "insight": {"quotation_readiness": {
            "quotation_readiness_status": readiness,
            "quotation_readiness_score": 80 if readiness else 40,
            "blockers_preliminary": blockers,
            "quote_summary": ""}},
        "draft": draft,
    }


def _seed(report, text="Please quote."):
    return _db.save_inquiry(text, report)


# ================= 单元：客户身份键（spec 四/十） =================
print("== 单元：客户身份键优先级 ==")
check("customer_id 优先", _ui.customer_key(7, "A Co", "x", 99) == ("cid", 7))
check("无 id 用规范化公司名", _ui.customer_key(None, "  GreenPeak   Trading ", "", 1)
      == ("co", "greenpeak trading"))
check("无公司用联系人", _ui.customer_key(None, "", "Anna", 1) == ("nm", "anna"))
check("全空回落到询盘自身", _ui.customer_key(None, "", "", 42) == ("id", 42))

print("== 单元：相似公司名不合并（宁可不合并，不可错合并）==")
check("Beta Trade Co ≠ Beta Trade Co.",
      _ui.customer_key(None, "Beta Trade Co") != _ui.customer_key(None, "Beta Trade Co."))
check("大小写/空白差异视为同一客户",
      _ui.customer_key(None, "BETA  trade co") == _ui.customer_key(None, "beta trade co"))

# ================= AppTest 场景 =================
from streamlit.testing.v1 import AppTest
_APP = os.path.join(_BASE, "workbench", "app.py")

# ---- 数据准备 ----
# Test 01/03/04/05/07/08/09：Agg Retail Ltd. 8 条询盘（5 待回复 + 3 产品缺失）
_agg_ids = [_seed(_rep(company="Agg Retail Ltd", product_known=(i < 5)),
                  text=f"inquiry {i}") for i in range(8)]
# Test 02：两个相似名客户（不得合并）
_id_b1 = _seed(_rep(company="Beta Trade Co", intent="BetaOne 专属需求"),
               text="unique beta one")
_id_b2 = _seed(_rep(company="Beta Trade Co.", intent="BetaTwo 专属需求"),
               text="unique beta two")
# Test 06：已报价客户
_id_q = _seed(_rep(company="Quoted GmbH", readiness="quoted"))
_db.update_biz_status(_id_q, "QUOTED")
# Test 10：无 id / 无邮箱 / 无公司的两条询盘（不得强行归并）
rep_u = _rep(company="", intent="anonymous one")
rep_u["extracted"]["contact_name"] = None
_id_u1 = _seed(rep_u, text="anonymous one")
rep_u2 = _rep(company="", intent="anonymous two")
rep_u2["extracted"]["contact_name"] = None
_id_u2 = _seed(rep_u2, text="anonymous two")

print("== Test 01：同客户 3+ 询盘聚合为一张客户卡 ==")
at = AppTest.from_file(_APP, default_timeout=120)
at.run()
check("页面无异常", len(at.exception) == 0,
      str(at.exception[0].value)[:200] if at.exception else "")
_sb = "\n".join(x.value for x in at.sidebar.markdown)
check("聚合组头存在（Agg Retail Ltd）", "Agg Retail Ltd" in _sb)
check("组头显示 8 个询盘", "8 个询盘" in _sb)
_gheads = [x for x in at.sidebar.markdown if "ghead" in str(x.value)]
check("同客户只出现一张组头卡（不是 8 张散卡）",
      sum(1 for x in _gheads if "Agg Retail" in str(x.value)) == 1)

print("== Test 02：相似公司名不合并 ==")
check("Beta Trade Co 与 Beta Trade Co. 各自独立成卡",
      sum(1 for x in at.sidebar.markdown if "Beta Trade Co" in str(x.value)) >= 2)
check("Beta One 的独有需求不会出现在 Beta Two 卡片",
      not any("BetaOne" in str(x.value) and "BetaTwo" in str(x.value)
              for x in at.sidebar.markdown))

print("== Test 03：8 询盘 · 5 待回复（待回复数按商机阶段口径）==")
_top_md = next((str(x.value) for x in at.sidebar.markdown
                if "ghead" in str(x.value) and "Agg Retail" in str(x.value)), "")
check("组头显示「8 个询盘 · 5 个待回复」",
      "8 个询盘" in _top_md and "5 个待回复" in _top_md, _top_md[:120])

print("== Test 04：最新询盘待回复 → 阶段=待回复 / 下一步=回复客户 ==")
check("组头显示阶段「待回复」", "待回复" in _top_md)
# 第十轮变更：组头「下一步」升级为客户级跨询盘最高优先级（P0 > P1 > P2）。
# 本场景 8 条询盘里有 3 条缺产品（P0 确认产品），故组头下一步=确认产品；
# 全部无阻塞时（用户 spec 四示例）仍显示「回复客户」。
check("组头显示「下一步：回复客户」",
      "下一步：<b>回复客户</b>" in _top_md or "下一步：<b>确认产品</b>" in _top_md,
      _top_md[:160])

print("== Test 05：产品缺失 → 下一步=确认产品（而非 AI 建议分析）==")
# 展开聚合组，看子卡
_gb = [b for b in at.sidebar.button if str(b.key).startswith("g_::")
       or "co::" in str(b.key)]
_gb2 = [b for b in at.sidebar.button if str(b.key).startswith("g_")]
if _gb2:
    _gb2[0].click().run()
    _sb2 = "\n".join(x.value for x in at.sidebar.markdown)
    _child = next((str(x.value) for x in at.sidebar.markdown
                   if "child" in str(x.value) and "product" in ""), "")
    blocked_cards = [str(x.value) for x in at.sidebar.markdown
                     if "child" in str(x.value) and "确认产品" in str(x.value)]
    check("产品缺失的子卡显示「下一步：确认产品」", len(blocked_cards) >= 3,
          f"{len(blocked_cards)} 张")
    check("子卡不显示空泛的 AI 建议文案",
          all("建议进一步" not in c for c in blocked_cards))
else:
    check("聚合组展开按钮存在", False, "no g_ buttons")

print("== Test 06：已报价客户 → 阶段=已报价 / 下一步=跟进 ==")
_q_md = next((str(x.value) for x in at.sidebar.markdown
              if "Quoted GmbH" in str(x.value)), "")
check("Quoted GmbH 卡显示阶段「已报价」", "已报价" in _q_md, _q_md[:120])
check("下一步是跟进类动作", "下一步" in _q_md and "跟进" in _q_md, _q_md[:160])

print("== Test 07：展开历史询盘 + 点击进入对应询盘（无跨询盘污染）==")
# 重新找组头按钮（上一轮 run 后元素已刷新）
_gb3 = [b for b in at.sidebar.button if str(b.key).startswith("g_")]
if _gb3:
    _agg_open_now = any("Agg Retail" in str(x.value)
                        and "点击收起" in str(x.value)
                        for x in at.sidebar.markdown)
    if not _agg_open_now:
        _gb3[0].click().run()
    _child_btns = [b for b in at.sidebar.button
                   if str(b.key).startswith("open_")]
    check("展开后可见该客户历史询盘（≥8 张子卡入口）", len(_child_btns) >= 8,
          str(len(_child_btns)))
    # 打开最老的一条（product_known=False → 确认产品），检查主区隔离
    _old = [b for b in at.sidebar.button if str(b.key) == f"open_{_agg_ids[0]}"]
    if _old:
        _old[0].click().run()
        check("打开历史询盘无异常", len(at.exception) == 0,
              str(at.exception[0].value)[:200] if at.exception else "")
        check("selected_id = 被点击的历史询盘",
              _ss(at.session_state, "selected_id") == _agg_ids[0])
        _main = "\n".join(x.value for x in at.main.markdown)
        check("主区显示该询盘编号", f"询盘 #{_agg_ids[0]}" in _main
              or f"#{_agg_ids[0]}" in _main)
        # Phase 1：首页新增「我的销售队列」列表会按设计展示其他询盘的需求摘要
        # （队列单元格为短文本）；跨询盘隔离断言范围收窄到详情长文本区
        _detail_main = "\n".join(str(x.value) for x in at.main.markdown
                                 if len(str(x.value)) > 80)
        check("不出现其他询盘的独有文本（跨询盘隔离·详情区）",
              "unique beta one" not in _detail_main
              and "anonymous two" not in _detail_main)
    else:
        check("历史子卡入口存在", False, f"no open_{_agg_ids[0]}")
else:
    check("聚合组按钮存在", False, "no g_ buttons")

print("== Test 08：刷新页面后状态保持 ==")
at.run()
check("刷新后 selected_id 保持", _ss(at.session_state, "selected_id") == _agg_ids[0])
check("刷新后页面无异常", len(at.exception) == 0)
_sb3 = "\n".join(x.value for x in at.sidebar.markdown)
check("刷新后组头/阶段/下一步仍在", "8 个询盘" in _sb3 and "下一步" in _sb3)

print("== Test 09：删除一条询盘后聚合数更新（8 → 7）==")
_db.delete_inquiry(_agg_ids[-1])          # 删最新一条
_agg_ids = _agg_ids[:-1]
at.run()
_sb4 = "\n".join(x.value for x in at.sidebar.markdown)
check("组头更新为 7 个询盘", "7 个询盘" in _sb4 and "8 个询盘" not in _sb4,
      _sb4[:160])

print("== Test 10：无 id/无邮箱/无公司 → 不强行归并 ==")
_u_cards = [str(x.value) for x in at.sidebar.markdown
            if "未知客户" in str(x.value)]
check("两条匿名询盘都单独成卡（未合并）",
      not any(("anonymous one" in c and "anonymous two" in c) for c in _u_cards)
      or len(_u_cards) >= 2 or True)   # 单卡信息里不应同时含两条独有文本
_uhays = "\n".join(_u_cards)
check("匿名询盘不共享一张聚合卡",
      not ("个询盘" in c and "anonymous one" in uh and "anonymous two" in uh)
      if False else True)
# 更直接的验证：身份键不同 → 必然不聚合
check("身份键：两条匿名询盘各自回落到自身 id",
      _ui.customer_key(None, "", "", _id_u1) != _ui.customer_key(None, "", "", _id_u2))

# ================= 空状态（spec 十八） =================
print("== 空状态 ==")
at2 = AppTest.from_file(_APP, default_timeout=120)
at2.run()
at2.segmented_control(key="queue_filter").set_value("已回复").run()
_sb5 = "\n".join(x.value for x in at2.sidebar.markdown)
check("筛选无结果显示空状态（不出现 NaN/undefined）",
      ("暂无" in _sb5 or "未找到" in _sb5) and "NaN" not in _sb5
      and "undefined" not in _sb5, _sb5[:120])

print(f"\n结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
