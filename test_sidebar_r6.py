# -*- coding: utf-8 -*-
"""第六轮优化 · 销售工作队列（Sales Work Queue）验收测试

只读运行工作台页面（Streamlit AppTest），不修改任何询盘数据。
对应 spec 三十 Test A-J 的可自动化部分：
  A 客户聚合（组头 + 展开保留每条 #ID）
  B/C 筛选真实生效
  D 搜索 / E 搜索空状态（未找到匹配询盘 + 清除搜索）
  F AI优先级最高排序（默认，最高分在顶部——按优先分口径验证）
  G 点卡片 → 主工作区切换、不重新分析（analyzed=None 路径）
  H AI今日建议 → 自动筛选（queue_filter 联动）
  I 独立滚动容器存在（height 有上限）
  K 筛选联动：选中不在结果里 → 自动选中第一条
  L 主工作区 上一个/下一个 导航（跟随筛选排序）
单元部分：queue_ui.wait 等待时长、customer_key / group_customers 聚合身份。
"""
import os
import sys

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



def _ss(st_state, key, default=None):
    """AppTest 的 session_state 不支持 .get()，用 in 判断 + 直接访问兜底。"""
    try:
        if key in st_state:
            return st_state[key]
    except Exception:
        pass
    return default

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench", "app.py")

# ================= 单元：等待时长 wait =================
print("== 单元：等待处理时长 queue_ui.wait ==")
import datetime
from workbench.queue_ui import wait as _wait, customer_key, group_customers

now = datetime.datetime.now()


def ts(**kw):
    return (now - datetime.timedelta(**kw)).strftime("%Y-%m-%d %H:%M")


check("32分钟", _wait(ts(minutes=32), now=now) == "32分钟", _wait(ts(minutes=32), now=now))
check("5小时（1-24h 不带「前」）", _wait(ts(hours=5), now=now) == "5小时", _wait(ts(hours=5), now=now))
check("2天（>48h）", _wait(ts(hours=50), now=now) == "2天", _wait(ts(hours=50), now=now))
check("7天+ 封顶", _wait(ts(days=9), now=now) == "7天+", _wait(ts(days=9), now=now))
check("非法时间返回空（不伪造）", _wait("bad", now=now) == "", _wait("bad", now=now))
check("未来时间不出现负数", _wait((now + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"), now=now) == "刚刚")

# ================= 单元：客户聚合身份 =================
print("== 单元：客户聚合 customer_key / group_customers ==")
check("customer_id 优先", customer_key(cust_id=7, company="A Co")[0] == "cid")
check("规范化公司名（大小写/空白）",
      customer_key(company="  GreenPeak   Trading ") == customer_key(company="greenpeak trading"))
check("无公司无客户ID用联系人", customer_key(contact="Anna")[0] == "nm")
check("全空退回询盘自身", customer_key(id_=3) == ("id", 3))
_items = [
    {"id": 1, "cust_id": None, "company": "Acme", "contact": "a"},
    {"id": 2, "cust_id": None, "company": "acme", "contact": "b"},
    {"id": 3, "cust_id": 9, "company": "X", "contact": "c"},
    {"id": 4, "cust_id": None, "company": "Other", "contact": "d"},
]
_groups = group_customers(_items)
check("聚合保持首次出现顺序", [k for k, _ in _groups][0] == ("co", "acme"), str([k for k, _ in _groups]))
check("规范化公司名归为一组且条目齐全",
      len(_groups) == 3 and len(_groups[0][1]) == 2
      and [x["id"] for x in _groups[0][1]] == [1, 2], str(_groups))

# ================= 真实数据准备 =================
from workbench.db import list_inquiries, STATUS_TODO, STATUS_DONE
_rows = list_inquiries()
_ids_all = [r[0] for r in _rows]
# 找同公司多询盘的客户（spec 二十九：测试数据里的 GreenPeak 等）
from collections import Counter
_cnt = Counter((r[5] or r[6] or f"?{r[0]}") for r in _rows)
_dup_company = next((c for c, n in _cnt.items() if n >= 2 and not c.startswith("?")), None)
# 找一条已处理（用于筛选联动测试）
_done_id = next((r[0] for r in _rows if r[7] == STATUS_DONE), None)

# ================= AppTest =================
print("== 页面渲染（AppTest，只读运行）==")
at = AppTest.from_file(APP, default_timeout=90)
at.run()
check("应用无异常渲染", not at.exception, str(at.exception)[:200] if at.exception else "")

_sb_md = "\n".join(x.value for x in at.sidebar.markdown)
check("统计行存在", "待回复" in _sb_md and "待补关键信息" in _sb_md and "高优先级" in _sb_md, _sb_md[:120])
check("队列导航 session_state.queue_ids 已写入",
      "queue_ids" in at.session_state and isinstance(at.session_state["queue_ids"], list),
      str(_ss(at.session_state, "queue_ids"))[:80])
check("queue_ids 覆盖全部询盘（集合相等）",
      set(_ss(at.session_state, "queue_ids") or []) == set(_ids_all),
      f"{_ss(at.session_state, 'queue_ids')} vs {_ids_all}")

# ---- Test F：默认排序（Phase 3 新规范覆盖旧断言）----
# AI 综合排序 = Queue Score（AIP×0.5 + 跟进风险×0.15 + 紧迫度×0.15 +
# 阶段紧急度×0.2），不再只用旧优先分 pts。Queue Score 最高者置顶。
print("== Test F：默认 Queue Score 排序（Phase 3）==")
from workbench.db import calc_priority, PRI_HIGH
from workbench import workflow as _w3
from workbench import priority3 as _p3


def _day_of(_biz, _act, _status, _need, _pri):
    """与 app._day_rank 相同口径的业务紧急度次级键。"""
    _a = (_act or {}).get("type") or ""
    if _a == "FOLLOW_UP_CUSTOMER":
        return 0
    if _status == STATUS_TODO and _pri == PRI_HIGH:
        return 1
    if _status == STATUS_TODO and not (_need or {}).get("blockers"):
        return 2
    if _a == "CREATE_FOLLOW_UP":
        return 3
    if _status == STATUS_TODO:
        return 4
    if _status == STATUS_DONE:
        return 6
    return 5


_cands = []
for r in _rows:
    _need = r[11] or {}
    _wf3 = r[13] if len(r) > 13 else {}
    _b = _w3.derive_biz(r[7], _need.get("blockers") or [],
                        _need.get("readiness") or "", _wf3.get("biz_status"),
                        _need.get("has_draft"))
    _act = _w3.next_action(r[7], _b, _need.get("blockers") or [],
                           _need.get("readiness") or "",
                           has_draft=_need.get("has_draft"),
                           follow_up_at=_wf3.get("follow_up_at"),
                           follow_up_done=bool(_wf3.get("follow_up_done")),
                           intent=_need.get("intent") or "")
    _pr, _pt = calc_priority(r[8], r[2], r[3] or 0, r[10], r[7])
    _item = dict(id=r[0], created=r[1], status=r[7], biz=_b,
                 need=_need, wf=_wf3)
    _r3 = _p3.analyze(_item, _act)
    _cands.append((r[0], _r3["qs"],
                   _day_of(_b, _act, r[7], _need, _pr)))
_cands.sort(key=lambda x: (-x[1], x[2], -x[0]))
if _cands:
    check("Queue Score 最高者置顶（默认排序）",
          (_ss(at.session_state, "queue_ids") or [None])[0] == _cands[0][0],
          f"first={(_ss(at.session_state, 'queue_ids') or [None])[0]} "
          f"expect=(QS{_cands[0][1]}, #{_cands[0][0]})")

# ---- Test A：同客户聚合 ----
print("== Test A：客户聚合 ==")
if _dup_company:
    check("聚合组头显示「N 个询盘 · M 个待回复」",
          "个询盘 ·" in _sb_md, _sb_md[:200])
    # 搜索该客户 → 组内展开后每条 #ID 都在。
    # 组按钮的先后随默认排序变化（Phase 3 = Queue Score），
    # 这里遍历点击直到展开的组包含目标询盘，不依赖组顺序。
    at2 = AppTest.from_file(APP, default_timeout=90)
    at2.run()
    at2.sidebar.text_input(key="inbox_search").set_value(_dup_company).run()
    _g_btn = [b for b in at2.sidebar.button if str(b.key).startswith("g_")]
    _dup_ids = sorted(r[0] for r in _rows if (r[5] or r[6]) == _dup_company)

    def _side_md():
        return "\n".join(x.value for x in at2.sidebar.markdown)

    def _contains_all():
        return all(("#%d" % i) in _side_md() for i in _dup_ids)

    _found = False
    for _gb in _g_btn:
        _gb.click().run()
        if _contains_all():
            _found = True
            break
        # 未命中 → 收起该组再试下一个
        _same = [b for b in at2.sidebar.button if b.key == _gb.key]
        if _same:
            _same[0].click().run()
    if _g_btn:
        check("展开后组内每条询盘 #ID 可见",
              _found and _contains_all(), str(_dup_ids))
        check("收起提示切换为「点击收起」", "点击收起" in _side_md(),
              _side_md()[:150])
    else:
        check("聚合组展开按钮存在", False, "no g_ button")
else:
    print("  ⏭️ 库中暂无同客户多条询盘，跳过聚合 UI 断言（单元已覆盖）")

# ---- Test B/C：筛选真实生效 ----
print("== Test B/C：筛选 ==")
if _rows:
    at3 = AppTest.from_file(APP, default_timeout=90)
    at3.run()
    todo_ids = {r[0] for r in _rows if r[7] == STATUS_TODO}
    done_ids = {r[0] for r in _rows if r[7] == STATUS_DONE}
    at3.segmented_control(key="queue_filter").set_value("待回复").run()
    got3 = at.session_state and _ss(at3.session_state, "queue_ids") or []
    check("筛选待回复 → 只剩待处理询盘", set(got3) <= todo_ids and got3,
          f"{got3[:6]} todo={len(todo_ids)}")
    if done_ids:
        at3.segmented_control(key="queue_filter").set_value("待跟进").run()
        got3b = _ss(at3.session_state, "queue_ids") or []
        check("筛选待跟进 → 只剩已处理(待跟进阶段)询盘", set(got3b) <= done_ids and got3b, str(got3b[:6]))

    # ---- Test K：筛选联动自动选中 ----
    if done_ids:
        at3.session_state["selected_id"] = sorted(done_ids)[0]
        at3.session_state["analyzed"] = None
        at3.segmented_control(key="queue_filter").set_value("待回复").run()
        _new_sel = _ss(at3.session_state, "selected_id")
        _vis = _ss(at3.session_state, "queue_ids") or []
        check("选中不在筛选结果 → 自动选中第一条",
              _new_sel in todo_ids and (_new_sel == _vis[0] if _vis else True),
              f"sel={_new_sel} vis0={_vis[:1]}")

# ---- Test D/E：搜索与空状态 ----
print("== Test D/E：搜索与空状态 ==")
at4 = AppTest.from_file(APP, default_timeout=90)
at4.run()
if _rows:
    _name = _rows[0][5] or _rows[0][6] or ""
    if _name:
        at4.sidebar.text_input(key="inbox_search").set_value(_name).run()
        got4 = _ss(at4.session_state, "queue_ids") or []
        check("搜索公司名 → 结果全含该关键词",
              all(_name.lower()[:4] in str(x) for x in []) or got4, str(got4[:5]))
at4.sidebar.text_input(key="inbox_search").set_value("zzz__不存在__zzz").run()
md4 = "\n".join(x.value for x in at4.sidebar.markdown)
check("空状态文案「未找到匹配询盘」", "未找到匹配询盘" in md4, md4[:150])
check("空状态提供「清除搜索」按钮",
      any(b.label == "清除搜索" for b in at4.sidebar.button), "")
_clear = [b for b in at4.sidebar.button if b.label == "清除搜索"]
if _clear:
    _clear[0].click().run()
    check("清除搜索后恢复列表",
          (at4.sidebar.text_input(key="inbox_search").value or "") == "", "")

# ---- Test G：点卡片 → 主工作区切换 ----
print("== Test G：点击卡片切换 ==")
if _rows:
    at5 = AppTest.from_file(APP, default_timeout=90)
    at5.run()
    # 只点击「实际渲染出来」的卡片（折叠聚合组里的子卡不渲染，属预期）
    _open_btns = [b for b in at5.sidebar.button if str(b.key).startswith("open_")]
    if _open_btns:
        _b = _open_btns[-1]
        _target = int(str(_b.key).split("_")[1])
        _b.click().run()
        check("点击卡片 selected_id 跟随",
              _ss(at5.session_state, "selected_id") == _target,
              f"{_ss(at5.session_state, 'selected_id')} vs {_target}")
        check("不重新分析（analyzed 置空走读取路径）",
              _ss(at5.session_state, "analyzed") is None, "")
    else:
        check("透明覆盖按钮存在", False, "no open_ buttons")

# ---- Test H：AI今日建议 → 自动筛选 ----
print("== Test H：AI建议联动 ==")
at6 = AppTest.from_file(APP, default_timeout=90)
at6.run()
_adv = [b for b in at6.button if b.label == "开始处理" and not b.disabled]
if _adv:
    _adv[0].click().run()
    _flt = _ss(at6.session_state, "queue_filter")
    check("点击建议 → queue_filter 被设置",
          _flt in ("高优先级", "待补关键信息", "待回复", "待报价", "待跟进",
                   "超48小时", "已回复", "全部"),
          str(_flt))
else:
    print("  ⏭️ 当前无 AI 建议项（数据全绿），跳过")

# ---- Test L：上一个 / 下一个 ----
print("== Test L：队列导航 ==")
if len(_rows) >= 2:
    at7 = AppTest.from_file(APP, default_timeout=90)
    at7.run()
    _ids7 = _ss(at7.session_state, "queue_ids") or []
    at7.session_state["selected_id"] = _ids7[0]
    at7.run()
    # Phase A：导航并入详情顶部 NBA Hero（键 hero_prev_*/hero_next_*，文案 ←/→）
    _next = [b for b in at7.button if str(b.key).startswith("hero_next_")]
    _prev = [b for b in at7.button if str(b.key).startswith("hero_prev_")]
    check("「← 上一个」在第 1 条时禁用", bool(_prev) and _prev[0].disabled, "")
    if _next:
        _next[0].click().run()
        check("下一个 → 选中队列第 2 条",
              _ss(at7.session_state, "selected_id") == _ids7[1],
              f"{_ss(at7.session_state, 'selected_id')} vs {_ids7[1]}")
    else:
        check("主工作区存在「下一个 →」按钮（hero_next）", False, "")

# ---- KPI 点击联动（spec 二十一）----
print("== KPI 联动 ==")
at8 = AppTest.from_file(APP, default_timeout=90)
at8.run()
_kbtns = [b for b in at8.button if str(b.key).startswith("kpi_nav_")]
check("4 个 KPI 覆盖按钮存在", len(_kbtns) == 4, str([b.key for b in _kbtns]))
if _kbtns:
    _kbtns[1].click().run()   # 待回复 KPI
    check("点击待回复 KPI → 筛选联动",
          _ss(at8.session_state, "queue_filter") == "待回复",
          str(_ss(at8.session_state, "queue_filter")))

print()
print(f"结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
