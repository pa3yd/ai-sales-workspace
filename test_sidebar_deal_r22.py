# -*- coding: utf-8 -*-
"""第二十二轮 · 左侧 Sidebar Deal 聚合与重复展示修复（Deal Threading）验收测试

只读运行 + 临时库隔离，不修改任何业务数据。
验收（对应长文 §16–§21 的可自动化部分）：
  A 同一个 Deal 不再重复显示（多封往来 = 一张主卡 + 展开历史）
  B 历史 Inquiry/Reply 完整保留（展开后每条 #ID 可见）
  C 同客户不同产品 = 两张独立 Deal 卡（绝不按公司名去重）
  D 产品未知时退回单条隔离（不误并两个 Deal）
  E 终态(已成交/丢单)与新的活跃往来分开成卡
  F Sidebar 状态/下一步来自 Deal 当前状态（商机阶段/跟进），不是旧消息
  G 统计单位明确（条 询盘/消息 · 个 待办 Deal）
  H 排序基于 Deal（同一 Deal 只参与一次）
"""
import os
import sys
import json
import shutil
import tempfile
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench"))

from streamlit.testing.v1 import AppTest

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


# ================= 单元：Deal 身份 / 聚合 / 当前状态 =================
print("== 单元：deal_thread_key / group_deal_threads / deal_current_state ==")
from workbench.queue_ui import (
    deal_thread_key, group_deal_threads, deal_current_state, priority_tier,
)

# 同客户 + 同产品 → 同一 Deal（即便多次分析、多次页面刷新也只一张主卡）
_d1 = dict(id=1, cust_id=10, company="BrightPromo BV", status="待处理",
           need={"product_query": "stainless steel water bottles", "qty": "约 10,000 pcs"},
           biz="READY_TO_REPLY", action={"label": "查看并发送回复", "type": "REVIEW_REPLY"},
           fu_state="", created="2026-09-09 10:00")
_d2 = dict(id=2, cust_id=10, company="BrightPromo BV", status="待处理",
           need={"product_query": "stainless steel water bottles 10000pcs logo", "qty": "约 10,000 pcs"},
           biz="READY_TO_REPLY", action={"label": "查看并发送回复", "type": "REVIEW_REPLY"},
           fu_state="", created="2026-09-09 11:00")
# 同客户 + 不同产品 → 不同 Deal
_mug = dict(id=3, cust_id=10, company="BrightPromo BV", status="待处理",
            need={"product_query": "Insulated Travel Mugs", "qty": "约 20,000 pcs"},
            biz="READY_TO_REPLY", action={"label": "查看并发送回复", "type": "REVIEW_REPLY"},
            fu_state="", created="2026-09-09 12:00")

check("同客户同产品 → 同一把 Deal 键", deal_thread_key(_d1) == deal_thread_key(_d2),
      str(deal_thread_key(_d1)) + " vs " + str(deal_thread_key(_d2)))
check("同客户不同产品 → 不同 Deal 键（不按公司名去重）",
      deal_thread_key(_d1) != deal_thread_key(_mug),
      str(deal_thread_key(_mug)))

_gs = group_deal_threads([_d2, _d1, _mug])
check("3 条往来（2 同一产品 + 1 新产品）聚合为 2 个 Deal",
      len(_gs) == 2, str([(g["count"], g["lead"]["id"]) for g in _gs]))
_g_bottle = next(g for g in _gs if g["count"] == 2)
check("组 lead = 排序最靠前（最该先处理）的往来",
      _g_bottle["lead"]["id"] == _d2["id"], str(_g_bottle["lead"]["id"]))
_st = deal_current_state(_g_bottle)
check("deal_current_state：往来数与待处理数",
      _st["conversation_count"] == 2 and _st["open_count"] == 2, str(_st))
check("deal_current_state：最近往来时间取组内最新",
      _st["last_created"] == "2026-09-09 11:00", _st["last_created"])
check("deal_current_state：lead 信息输出完整",
      _st["lead_id"] == 2 and _st["nba"] == "查看并发送回复"
      and _st["tier"] == priority_tier(_d2), str(_st))

# 产品未知：退回单条隔离（宁可不合并，也不误并）
_np1 = dict(id=5, cust_id=20, company="Acme Co", status="待处理",
            need={}, biz="NEW", action={}, fu_state="", created="2026-09-09 09:00")
_np2 = dict(id=6, cust_id=20, company="Acme Co", status="待处理",
            need={}, biz="NEW", action={}, fu_state="", created="2026-09-09 09:10")
_gnp = group_deal_threads([_np1, _np2])
check("产品未知 → 单条隔离（不把两个未知 Deal 误并）",
      len(_gnp) == 2, str(len(_gnp)))

# 终态（WON）与新的活跃同产品往来分开成卡
_lost = dict(id=7, cust_id=30, company="Zeta AB", status="已处理",
             need={"product_query": "Wireless Earbuds"}, biz="REPLIED",
             action={}, fu_state="", created="2026-09-01 09:00")
_new = dict(id=8, cust_id=30, company="Zeta AB", status="待处理",
            need={"product_query": "Wireless Earbuds"}, biz="NEW",
            action={"label": "分析询盘"}, fu_state="", created="2026-09-09 09:00")
_stage_of = {7: "WON", 8: "NEW"}
_gz = group_deal_threads([_lost, _new], _stage_of)
check("已成交历史与新的活跃往来分开成卡",
      len(_gz) == 2, str([(g["count"], g["key"][3]) for g in _gz]))

# ================= 临时库 UI：BrightPromo 3 瓶 + 1 杯 =================
print("== UI（临时库）：重复合并 + 同客户双 Deal ==")
# 注意：必须用顶层 import db —— AppTest 内 app.py 也是 `import db`，
# 这样改 DB_PATH 才会在同一模块对象上生效（与 T03 UI 冒烟同法）。
import db as _db


def _rep(product, qty, iid_tag):
    return {
        "extracted": {
            "company": "BrightPromo BV", "country": "Netherlands",
            "contact_name": "Sophie", "quantity": qty,
            "quantity_unit": "pcs", "product_query": product,
            "intent": "目录/报价请求",
        },
        "lead": {"grade": "B", "score": 50},
        "matches": [], "insight": {}, "score": 50, "draft": "",
    }


_tmp = tempfile.mkdtemp()
_tmpdb = os.path.join(_tmp, "workbench.db")
_old = _db.DB_PATH
_db.DB_PATH = _tmpdb
try:
    _db.init_db()
    _ids_b = [_db.save_inquiry(
        "Hi, quote for Stainless Steel Water Bottles 10,000 pcs.",
        _rep("Stainless Steel Water Bottles", 10000, i)) for i in range(3)]
    _id_mug = _db.save_inquiry(
        "Quote Insulated Travel Mugs 20,000 pcs please.",
        _rep("Insulated Travel Mugs", 20000, "m"))
    # 把水瓶组代表商机推进到「需求已确认」→ 主卡应显示 Deal 阶段，
    # 而不是某条旧消息的业务状态（§11/§12 / §F）
    _b_opp = next(o for o in _db.list_opportunities()
                  if o.get("inquiry_id") == _ids_b[0])
    _db.move_opportunity_stage(_b_opp["id"], "REQUIREMENT_CONFIRMED")

    at = AppTest.from_file(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "workbench", "app.py"),
                           default_timeout=90).run()
    check("应用无异常渲染", len(at.exception) == 0,
          str(at.exception[0].value)[:200] if at.exception else "")
    _sb_md = "\n".join(x.value for x in at.sidebar.markdown)
    _open_ids = [int(str(b.key).split("_")[1]) for b in at.sidebar.button
                 if str(b.key).startswith("open_")]
    check("4 条往来只渲染 2 张主卡（同瓶 3 封 → 1 卡；杯 1 封 → 1 卡）",
          len(_open_ids) == 2, str(_open_ids))
    check("统计单位明确（条 vs 个）",
          "条询盘" in _sb_md and "按 Deal 聚合" in _sb_md and "个" in _sb_md,
          _sb_md[:200])
    check("两张卡都是 BrightPromo BV（同客户不同 Deal 分开，不重复）",
          _sb_md.count("BrightPromo BV") >= 2,
          f"count={_sb_md.count('BrightPromo BV')}")
    check("卡 1 产品 = 水瓶短名", "Stainless Steel Water Bottles" in _sb_md, "")
    check("卡 1 数量 = 10,000", "10,000" in _sb_md, "")
    check("卡 2 产品 = 旅行杯短名（不同 Deal 独立成卡）",
          "Insulated Travel Mugs" in _sb_md, "")
    check("卡 2 数量 = 20,000", "20,000" in _sb_md, "")
    # §F：水瓶组主卡显示 Deal 当前阶段（需求已确认）与商机下一步，
    #     而不是从旧 Inquiry 消息级状态拼「待回复」
    check("状态来自 Deal 当前阶段（需求已确认）", "需求已确认" in _sb_md, "")
    check("下一步来自商机推荐（生成并发送报价）",
          "生成并发送报价" in _sb_md, "")
    check("聚合主卡显示「3 条往来」", "3 条往来" in _sb_md, "")
    _sd = [b for b in at.sidebar.button if str(b.key).startswith("sd_")]
    check("水瓶组有「展开历史」按钮，单条旅行杯无",
          len(_sd) == 1 and "2 条历史往来" in str(_sd[0].label),
          str([(b.key, b.label) for b in _sd]))
    if _sd:
        _sd[0].click().run()
        _sb_md2 = "\n".join(x.value for x in at.sidebar.markdown)
        check("展开后水瓶 3 封历史 #ID 全部可见（历史不丢）",
              all(("#%d" % i) in _sb_md2 for i in _ids_b),
              str(_ids_b))
        check("展开后旅行杯 #ID 仍可见",
              ("#%d" % _id_mug) in _sb_md2, str(_id_mug))
finally:
    _db.DB_PATH = _old
    shutil.rmtree(_tmp, ignore_errors=True)

# ================= 排序基于 Deal（每 Deal 只排一次） =================
print("== 单元：Deal 排序去重 ==")
# 同一 Deal 的 3 封消息放进已排序输入 → 输出只有 1 个该 Deal 的位次
_dup3 = group_deal_threads([
    dict(id=1, cust_id=10, company="BrightPromo BV",
         need={"product_query": "water bottles"}, biz="READY_TO_REPLY",
         action={}, fu_state="", status="待处理", created="2026-09-09 08:00"),
    dict(id=2, cust_id=10, company="BrightPromo BV",
         need={"product_query": "water bottles"}, biz="READY_TO_REPLY",
         action={}, fu_state="", status="待处理", created="2026-09-09 09:00"),
    dict(id=3, cust_id=10, company="BrightPromo BV",
         need={"product_query": "water bottles"}, biz="READY_TO_REPLY",
         action={}, fu_state="", status="待处理", created="2026-09-09 10:00"),
])
check("同一 Deal 在排序队列只占一个位次", len(_dup3) == 1,
      str([(g["count"]) for g in _dup3]))

print()
print(f"结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
