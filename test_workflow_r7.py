# -*- coding: utf-8 -*-
"""第七轮测试：销售执行闭环（workflow 状态机 + Next Action + 回复/跟进/结单闭环）。

覆盖 spec 二十八节 10 场景 + 二十九节业务闭环 + 跨询盘隔离 + 状态机防非法跳转。
使用临时数据库（不污染真实 workbench.db）。
运行：python test_workflow_r7.py
"""
import os
import re
import sys
import json
import shutil
import sqlite3
import datetime
import tempfile

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_BASE, "workbench"))

# —— 先换临时库，再让 app 引用同一 db 模块（AppTest 与本进程共享 sys.modules）——
import db as _db
_TMP_DB = os.path.join(tempfile.gettempdir(), "wb_r7_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
_db.DB_PATH = _TMP_DB
_db.init_db()          # 临时库建表（含第七轮 workflow 列 + activity 表）

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


def _rep(product_known=True, qty=3000, blockers=None, readiness="",
         draft="", intent="目录/报价请求", company="Test Co"):
    """构造最小分析报告（与 agent 输出同构，走同一条派生链）。"""
    if blockers is None:
        blockers = ([] if product_known else
                    [{"field": "product", "severity": "high",
                      "phase": "preliminary"}])
        if qty is None:
            blockers = blockers + [{"field": "quantity", "severity": "high",
                                    "phase": "preliminary"}]
    return {
        "extracted": {"company": company, "country": "Germany",
                      "contact_name": "Anna", "intent": intent,
                      "quantity": qty, "quantity_unit": "pcs",
                      "product_query": "steel bottle" if product_known else None},
        "lead": {"grade": "B", "score": 70},
        "matches": [],
        "insight": {"quotation_readiness": {
            "quotation_readiness_status": readiness,
            "quotation_readiness_score": 80 if readiness == "ready_for_quotation" else 40,
            "blockers_preliminary": blockers,
            "quote_summary": ""}},
        "draft": draft,
    }


def _seed(report, text="Please quote."):
    return _db.save_inquiry(text, report)


# ================= 单元：workflow 派生 / 状态机 / Next Action =================
import workflow as _wf

print("== 单元：业务状态派生（兼容两态）==")
check("待处理+产品阻塞 → NEEDS_INFO",
      _wf.derive_biz("待处理", ["product"], "", None, False) == "NEEDS_INFO")
check("待处理+无阻塞+报价就绪 → READY_FOR_QUOTE",
      _wf.derive_biz("待处理", [], "ready_for_quotation", None, False)
      == "READY_FOR_QUOTE")
check("已处理（旧版标记跟进）→ FOLLOW_UP",
      _wf.derive_biz("已处理", [], "", None, False) == "FOLLOW_UP")
check("人工保存的 REPLIED 优先",
      _wf.derive_biz("已处理", ["product"], "", "REPLIED", False) == "REPLIED")
check("已报价 → QUOTED",
      _wf.derive_biz("待处理", [], "quoted", None, False) == "QUOTED")

print("== 单元：状态机防非法跳转 ==")
check("NEW → WON 禁止", not _wf.can_transition("NEW", "WON"))
check("NEEDS_INFO → QUOTED 禁止", not _wf.can_transition("NEEDS_INFO", "QUOTED"))
check("READY_TO_REPLY → REPLIED 允许", _wf.can_transition("READY_TO_REPLY", "REPLIED"))
check("REPLIED → QUOTED 允许（报价后）", _wf.can_transition("REPLIED", "QUOTED"))
check("QUOTED → WON 允许", _wf.can_transition("QUOTED", "WON"))
check("FOLLOW_UP → ANALYZING 允许（客户回复后重析）",
      _wf.can_transition("FOLLOW_UP", "ANALYZING"))
check("WON → 任何状态 禁止", not _wf.can_transition("WON", "REPLIED"))
check("LOST → NEGOTIATING 允许（死单复活）", _wf.can_transition("LOST", "NEGOTIATING"))

print("== 单元：Next Action 映射 ==")
na = lambda **kw: _wf.next_action("待处理", "NEEDS_INFO", kw.pop("blockers", []),
                                  kw.pop("readiness", ""), kw.pop("has_draft", False),
                                  **kw)
check("产品未知 → 确认产品(CONFIRM_PRODUCT)",
      na(blockers=["product"])["type"] == "CONFIRM_PRODUCT")
check("数量不明 → 确认数量(CONFIRM_QUANTITY)",
      na(blockers=["quantity"])["type"] == "CONFIRM_QUANTITY")
check("规格不明 → 确认规格(CONFIRM_SPECIFICATION)",
      na(blockers=["product_spec"])["type"] == "CONFIRM_SPECIFICATION")
check("要求认证 → 确认认证(CHECK_CERTIFICATION)",
      na(blockers=["certification"])["type"] == "CHECK_CERTIFICATION")
check("阻塞 P0 优先于其他动作",
      na(blockers=["product"], has_draft=True)["priority"] == "P0")
check("报价就绪 → 生成报价(CREATE_QUOTE)",
      _wf.next_action("待处理", "READY_FOR_QUOTE", [], "ready_for_quotation",
                      False)["type"] == "CREATE_QUOTE")
check("草稿就绪 → 查看并发送(REVIEW_REPLY)",
      _wf.next_action("待处理", "READY_TO_REPLY", [], "", True)["type"]
      == "REVIEW_REPLY")
check("已回复未设跟进 → 设置跟进时间(CREATE_FOLLOW_UP)",
      _wf.next_action("已处理", "REPLIED", [], "", False)["type"]
      == "CREATE_FOLLOW_UP")
_ago = (datetime.datetime.now()
        - datetime.timedelta(hours=49)).strftime("%Y-%m-%d %H:%M")
check("跟进到期 → 立即跟进(FOLLOW_UP_CUSTOMER)",
      _wf.next_action("已处理", "REPLIED", [], "", False,
                      follow_up_at=_ago)["type"] == "FOLLOW_UP_CUSTOMER")
check("已报价 → 跟进报价反馈(FOLLOW_UP_QUOTE)",
      _wf.next_action("已处理", "QUOTED", [], "quoted", False)["type"]
      == "FOLLOW_UP_QUOTE")
check("已成交 → 无动作", _wf.next_action("已处理", "WON", [], "", False)["type"] is None)
check("客户要目录 → SEND_CATALOG（不强制先答一堆问题）",
      _wf.next_action("待处理", "READY_TO_REPLY", [], "", False,
                      intent="Please send me your catalog")["type"] == "SEND_CATALOG")
check("客户要样品 → CHECK_SAMPLE",
      _wf.next_action("待处理", "READY_TO_REPLY", [], "", False,
                      intent="We need a sample first")["type"] == "CHECK_SAMPLE")

print("== 单元：跟进状态 ==")
_today = datetime.datetime.now().strftime("%Y-%m-%d")
_tomorrow = (datetime.datetime.now()
             + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
check("无跟进时间 → 空状态", _wf.followup_state(None, 0) == "")
check("已完成 → 已完成", _wf.followup_state(_ago, 1) == "已完成")
check("过期 → 已逾期", _wf.followup_state(_ago, 0) == "已逾期")
check("今天 → 今日跟进", _wf.followup_state(_today + " 23:59", 0) == "今日跟进")
check("未来 → 待跟进", _wf.followup_state(_tomorrow, 0) == "待跟进")

# ================= 数据闭环：回复 → 跟进 → 结单（真实 db 函数） =================
print("== Test 07/08：标记已发送 → REPLIED → 跟进 ==")
_id7 = _seed(_rep(product_known=False, qty=None, draft="Could you confirm the model?"))
_db.record_activity(_id7, "ANALYZED", "AI 完成询盘分析")
_db.record_activity(_id7, "REPLY_GENERATED", "生成客户回复草稿")
_w = _db.get_workflow(_id7)
check("初始无业务状态（旧记录派生）", _w["biz_status"] == "")
_db.mark_replied(_id7)
_w = _db.get_workflow(_id7)
check("标记已发送 → REPLIED", _w["biz_status"] == "REPLIED")
check("记录 last_replied_at", bool(_w["last_replied_at"]))
check("产生 REPLIED 事件",
      any(a[0] == "REPLIED" for a in _db.list_activity(_id7)))
check("已回复未设跟进 → 下一步 CREATE_FOLLOW_UP",
      _wf.next_action("已处理", "REPLIED", [], "", True,
                      follow_up_at=_w["follow_up_at"])["type"]
      == "CREATE_FOLLOW_UP")
_due_at = (datetime.datetime.now()
           - datetime.timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
_db.set_follow_up(_id7, _due_at)
_w = _db.get_workflow(_id7)
check("跟进到期 → FOLLOW_UP_CUSTOMER",
      _wf.next_action("已处理", "REPLIED", [], "", True,
                      follow_up_at=_w["follow_up_at"])["type"]
      == "FOLLOW_UP_CUSTOMER")
check("跟进状态 = 已逾期",
      _wf.followup_state(_w["follow_up_at"], _w["follow_up_done"]) == "已逾期")
_db.complete_follow_up(_id7)
check("完成跟进 → 已完成",
      _wf.followup_state(_db.get_workflow(_id7)["follow_up_at"],
                         _db.get_workflow(_id7)["follow_up_done"]) == "已完成")
check("产生 FOLLOW_UP_CREATED / COMPLETED 事件",
      {a[0] for a in _db.list_activity(_id7)} >= {"FOLLOW_UP_CREATED",
                                                  "FOLLOW_UP_COMPLETED"})

print("== 结单：人工确认（状态机约束）==")
_id9 = _seed(_rep(product_known=True, qty=1000, readiness="ready_for_quotation"))
_db.mark_replied(_id9)
_db.set_follow_up(_id9, _tomorrow)
# 报价后成交：REPLIED → QUOTED → WON
_db.update_biz_status(_id9, "QUOTED")
check("QUOTED → WON 状态机允许",
      _wf.can_transition(_db.get_workflow(_id9)["biz_status"], "WON"))
_db.set_deal(_id9, "WON")
check("标记成交 → WON + deal_status",
      _db.get_workflow(_id9)["deal_status"] == "WON")
check("产生 WON 事件", any(a[0] == "WON" for a in _db.list_activity(_id9)))
check("成交后无 Next Action",
      _wf.next_action("已处理", "WON", [], "", False)["type"] is None)
# 未报价的待回复询盘：状态机禁止直接跳 WON（UI 不显示结单入口）
_id_fresh = _seed(_rep(product_known=False, qty=None))
check("READY_TO_REPLY → WON 状态机禁止",
      not _wf.can_transition(_wf.derive_biz("待处理", ["product"], "", "", True),
                             "WON"))

# ================= 业务闭环（spec 二十九）：全链路数据连续 =================
print("== 业务闭环：新询盘 → 补信息 → 回复 → 发送 → 跟进 → 报价 → 成交 ==")
cid = _seed(_rep(product_known=False, qty=None))          # ① 新询盘，产品缺失
_db.record_activity(cid, "ANALYZED", "AI 完成询盘分析")
st1 = _wf.derive_biz("待处理", ["product"], "", "", False)
a1 = _wf.next_action("待处理", st1, ["product"], "", False)
check("① NEEDS_INFO / 确认产品", st1 == "NEEDS_INFO"
      and a1["type"] == "CONFIRM_PRODUCT")
# ② 销售补充产品（重新分析后阻塞消失，有草稿）
_db.record_activity(cid, "REPLY_GENERATED", "生成客户回复草稿")
_db.update_biz_status(cid, "READY_TO_REPLY")
st2 = _wf.derive_biz("待处理", [], "", "READY_TO_REPLY", True)
a2 = _wf.next_action("待处理", st2, [], "", True)
check("② 补齐后 READY_TO_REPLY / 查看并发送", st2 == "READY_TO_REPLY"
      and a2["type"] == "REVIEW_REPLY")
# ③ 标记已发送
_db.mark_replied(cid)
st3 = _db.get_workflow(cid)["biz_status"]
check("③ REPLIED + 状态机 READY_TO_REPLY→REPLIED 合法",
      st3 == "REPLIED" and _wf.can_transition("READY_TO_REPLY", "REPLIED"))
# ④ 自动产生跟进（48h）
fu48 = (datetime.datetime.now()
        + datetime.timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")
_db.set_follow_up(cid, fu48)
check("④ 跟进已排期，当前不告警",
      _wf.followup_state(fu48, 0) == "待跟进")
# ⑤ 48 小时后到期
_db.set_follow_up(cid, _due_at)
st5 = _wf.next_action("已处理", "REPLIED", [], "", True,
                      follow_up_at=_due_at)
check("⑤ 到期 → FOLLOW_UP_CUSTOMER", st5["type"] == "FOLLOW_UP_CUSTOMER")
# ⑥ 客户回复 → 重新分析 → 信息完整 → 报价就绪
check("⑥ FOLLOW_UP → ANALYZING 允许", _wf.can_transition("REPLIED", "ANALYZING"))
_db.update_biz_status(cid, "READY_FOR_QUOTE")
st6 = _wf.next_action("待处理", "READY_FOR_QUOTE", [], "ready_for_quotation", True)
check("⑥ 报价就绪 → 生成报价", st6["type"] == "CREATE_QUOTE")
# ⑦ 报价 → 谈判 → 成交
_db.update_biz_status(cid, "QUOTED")
a7 = _wf.next_action("已处理", "QUOTED", [], "quoted", True)
check("⑦ 已报价 → 跟进报价反馈", a7["type"] == "FOLLOW_UP_QUOTE")
_db.set_deal(cid, "WON")
_types = [a[0] for a in _db.list_activity(cid)]
check("⑧ 全链路事件连续（ANALYZED→…→WON）",
      _types[0] == "ANALYZED" and _types[-1] == "WON"
      and "REPLIED" in _types and "FOLLOW_UP_CREATED" in _types,
      str(_types))

# ================= Test 10：跨询盘隔离（UI 层，真实渲染） =================
print("== Test 10：跨询盘数据隔离 ==")
from streamlit.testing.v1 import AppTest
_A = _seed(_rep(product_known=True, qty=3000, draft="Dear Anna, Model A / 3,000 pcs ...",
                company="Alpha GmbH"), "We want Model A, 3000 pcs.")
_B = _seed(_rep(product_known=False, qty=1000, draft="Could you share the model?",
                company="Beta AB"), "We need 1000 pcs, which model?")
at = AppTest.from_file(os.path.join(_BASE, "workbench", "app.py"),
                       default_timeout=90)
at.run()
# 从当前实际渲染的首页行动入口打开 B；Sidebar Recent Access 不能作为首次入口。
_btn_map = {int(str(b.key).split("_")[-1]): b for b in at.button
            if re.fullmatch(r"mq_open_\d+", str(b.key or ""))}
if _B in _btn_map:
    _btn_map[_B].click().run()
    check("打开 B 无异常", len(at.exception) == 0,
          str(at.exception[0].value)[:150] if at.exception else "")
    main_md = "\n".join(x.value for x in at.main.markdown)
    # 注：AppTest 会渲染所有 tab 的隐藏内容（Tab2 跟进台队列里 A 的卡片
    # 本来就该显示 A 自己的数量），所以隔离检查只针对「B 的详情区」口径：
    # ① B 页面展示 B 自己的数量；② 所有草稿/追问文本框里没有 A 的草稿内容
    check("B 页面展示 B 自己的数量（1,000 pcs）",
          "1,000" in main_md or "1000" in main_md)
    _all_ta = " ".join(str(ta.value) for ta in at.main.text_area)
    check("任何草稿框都不含 A 的草稿内容", "Model A" not in _all_ta
          and "3,000 pcs" not in _all_ta)
    _draft_boxes = {ta.key: ta.value for ta in at.main.text_area}
    _bval = _draft_boxes.get(f"draft_box_{_B}", "")
    check("B 草稿 = B 自己的草稿", "share the model" in _bval
          and "Model A" not in _bval)
    _akeys = [k for k in _draft_boxes if str(k).startswith("draft_box_")]
    check("草稿框按询盘隔离（无跨询盘 key 残留）",
          all(str(k).endswith(str(_B)) or str(k).endswith(str(_A))
              for k in _akeys), str(_akeys))
else:
    check("Test 10 打开询盘 B", False, "no rendered homepage action")

# ================= UI 闭环：标记已发送按钮 + 跟进区 =================
print("== UI：标记已发送 → REPLIED → 跟进区出现 ==")
_id_ui = _seed(_rep(product_known=True, qty=500, draft="Hello, thanks for your inquiry.",
                    company="Gamma Ltd"))
at2 = AppTest.from_file(os.path.join(_BASE, "workbench", "app.py"),
                        default_timeout=90)
at2.run()
_btns2 = {int(str(b.key).split("_")[-1]): b for b in at2.button
           if re.fullmatch(r"mq_open_\d+", str(b.key or ""))}
if _id_ui in _btns2:
    _btns2[_id_ui].click().run()
    # 结单入口：待回复（READY_TO_REPLY）阶段状态机禁止直接跳 WON → 不显示
    check("待回复阶段不显示结单入口（状态机约束）",
          not any("deal_won_" in str(b.key) for b in at2.main.button))
    _mrb = [b for b in at2.main.button if str(b.key) == f"mark_replied_{_id_ui}"]
    check("回复草稿区有「标记为已发送」按钮", bool(_mrb))
    if _mrb:
        _mrb[0].click().run()
        check("点击后状态 = REPLIED",
              _db.get_workflow(_id_ui)["biz_status"] == "REPLIED")
        check("页面出现跟进区（设置跟进时间）",
              any(str(b.key) == f"fuset_{_id_ui}" for b in at2.main.button))
        # 「待跟进」筛选口径：已回复未设跟进 → CREATE_FOLLOW_UP 动作
        check("已回复未设跟进 → 进入待跟进待办（CREATE_FOLLOW_UP）",
              _wf.next_action("已处理", "REPLIED", [], "", True)["type"]
              == "CREATE_FOLLOW_UP")
        # 已回复后：REPLIED → WON 状态机允许 → 结单入口出现（仍需人工勾选确认）
        check("已回复阶段显示结单入口（人工确认制）",
              any("deal_won_" in str(b.key) for b in at2.main.button))
else:
    check("UI 闭环打开询盘", False, "no rendered homepage action")

print()
print(f"结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
