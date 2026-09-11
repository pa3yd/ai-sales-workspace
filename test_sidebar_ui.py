# -*- coding: utf-8 -*-
"""左侧询盘队列 UI/UX 优化 · 验收测试（spec 二十四 Test A-J 的可自动化部分）

只读运行工作台页面（Streamlit AppTest），不修改任何询盘数据。
覆盖：
  - 应用可正常渲染（无异常）
  - 队列统计行 / 搜索框 / 排序下拉 / 空状态
  - 卡片信息层级：状态 > 客户 > 主题·数量 > 国家·时间 > #ID > 分数
  - 相对时间与国旗（单元）
  - 选中高亮 sel class
  - 搜索扩展字段（ID / 主题 / 数量）
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


# ---------- 单元：相对时间 / 国旗（queue_ui 无 Streamlit 依赖） ----------
print("== 单元：相对时间与国旗 ==")
import datetime
from workbench.queue_ui import ago as _ago, flag as _flag

now = datetime.datetime.now()


def ts(**kw):
    return (now - datetime.timedelta(**kw)).strftime("%Y-%m-%d %H:%M")


check("刚刚", _ago(ts(seconds=0), now=now) == "刚刚", _ago(ts(seconds=0), now=now))
check("12分钟前", _ago(ts(minutes=12), now=now) == "12分钟前", _ago(ts(minutes=12), now=now))
check("2小时前", _ago(ts(hours=2), now=now) == "2小时前", _ago(ts(hours=2), now=now))
check("昨天", _ago(ts(hours=30), now=now) == "昨天", _ago(ts(hours=30), now=now))
check("3天前", _ago(ts(days=3), now=now) == "3天前", _ago(ts(days=3), now=now))
_8d = _ago(ts(days=8), now=now)
check("超过7天显示「9月2日」式", ("月" in _8d and "日" in _8d), _8d)
check("国旗 UK", _flag("United Kingdom") == "🇬🇧", _flag("United Kingdom"))
check("国旗 德国", _flag("德国") == "🇩🇪", _flag("德国"))
check("未知国家不给旗（不虚构）", _flag("Atlantis") == "", _flag("Atlantis"))
check("空国家不给旗", _flag("") == "", _flag(""))

# ---------- 读取真实队列数据（只读） ----------
from workbench.db import list_inquiries, STATUS_TODO
_rows = list_inquiries()
_first_id = _rows[0][0] if _rows else None
_no_qty_company = None
for r in _rows:
    need = r[11] if len(r) > 11 else {}
    if not (need or {}).get("qty"):
        _no_qty_company = r[5] or r[6]
        break
_same_company = None
if len(_rows) >= 2:
    from collections import Counter
    cnt = Counter((r[5] or r[6] or "?") for r in _rows)
    dup = [c for c, n in cnt.items() if n >= 2 and c != "?"]
    _same_company = dup[0] if dup else None

# ---------- AppTest：完整页面渲染 ----------
print("== 页面渲染（AppTest，只读运行）==")
at = AppTest.from_file(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "workbench", "app.py"), default_timeout=60)
at.run()
check("应用无异常渲染", not at.exception,
      str(at.exception)[:200] if at.exception else "")

_sb_md = "\n".join(x.value for x in at.sidebar.markdown)
_sb_btns = " ".join(b.label for b in at.sidebar.button)
check("队列统计行存在（待回复/待补关键信息/高优先级）",
      "待回复" in _sb_md and "待补关键信息" in _sb_md and "高优先级" in _sb_md,
      _sb_md[:120])
check("搜索框新 placeholder", "搜索客户、公司、产品或询盘" in
      (at.sidebar.text_input(key="inbox_search").placeholder or ""),
      at.sidebar.text_input(key="inbox_search").placeholder or "")
_opts = at.sidebar.selectbox(key="queue_sort").options
check("排序下拉含 7 个选项（第二十二轮：AI综合+今日待办等）",
      len(_opts) == 7 and "AI 综合排序（Queue Score）" in _opts
      and "今日待办优先" in _opts, str(_opts))

# 卡片结构：状态在客户名之前、#ID、分数右下（HTML 里顺序即可断言层级）
if _rows:
    # 第六轮：多询盘客户默认折叠为组，单条卡片仍带 #真实ID ——
    # 用实际渲染出的 open_ 覆盖按钮对应检查，不依赖折叠状态
    _render_ids = [int(str(b.key).split("_")[1]) for b in at.sidebar.button
                   if str(b.key).startswith("open_")]
    check("渲染卡片含 #真实ID（区分同客户多询盘）",
          all(("#%d" % i) in _sb_md for i in _render_ids) and len(_render_ids) >= 1,
          str(_render_ids[:5]))
    _open_keys = [b.key for b in at.sidebar.button if str(b.key).startswith("open_")]
    check("整卡可点击（透明覆盖按钮存在）", len(_open_keys) >= 1, str(_open_keys[:5]))
    check("旧「打开 →」按钮已删除", "打开 →" not in _sb_btns)

# ---------- Test D：数量未识别 ----------
print("== Test D：数量未识别 ==")
if _no_qty_company:
    at2 = AppTest.from_file(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "workbench", "app.py"), default_timeout=60)
    at2.run()
    at2.sidebar.text_input(key="inbox_search").set_value(_no_qty_company).run()
    md2 = "\n".join(x.value for x in at2.sidebar.markdown)
    check("数量未识别（不是 0 pcs）", "数量未识别" in md2 and "0 pcs" not in md2,
          md2[:200])
else:
    print("  ⏭️ 库中暂无缺数量的询盘，跳过")

# ---------- Test G/H：搜索客户名 / ID / 主题 ----------
print("== Test G/H：搜索 ==")
if _rows:
    at3 = AppTest.from_file(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "workbench", "app.py"), default_timeout=60)
    at3.run()
    # 按客户名搜索
    _name = _rows[0][5] or _rows[0][6] or ""
    if _name:
        at3.sidebar.text_input(key="inbox_search").set_value(_name).run()
        md3 = "\n".join(x.value for x in at3.sidebar.markdown)
        check("搜索客户名有结果且含该公司", _name.split()[0][:4] in md3, md3[:150])
    # 搜索不存在的关键词 → 空状态
    at3.sidebar.text_input(key="inbox_search").set_value("zzz__不存在__zzz").run()
    md3b = "\n".join(x.value for x in at3.sidebar.markdown)
    check("空状态：未找到匹配的询盘", "未找到匹配询盘" in md3b, md3b[:150])
    # 按询盘 ID 搜索
    at3.sidebar.text_input(key="inbox_search").set_value(str(_rows[0][0])).run()
    md3c = "\n".join(x.value for x in at3.sidebar.markdown)
    check("搜索询盘 ID 可命中", ("#%d" % _rows[0][0]) in md3c, md3c[:150])

# ---------- Test J：选中高亮 & 左右对应 ----------
print("== Test J：选中状态 ==")
if _first_id is not None:
    at4 = AppTest.from_file(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "workbench", "app.py"), default_timeout=60)
    at4.run()
    at4.session_state["selected_id"] = _first_id
    at4.run()
    md4 = "\n".join(x.value for x in at4.sidebar.markdown)
    check("选中卡片带 sel class", "sq " in md4 and " sel" in md4, md4[:200])
    # 点击卡片（透明覆盖按钮）→ selected_id 跟随
    # 注意：折叠的聚合组内的子卡片不渲染，必须用实际渲染出来的 open_ 按钮
    _open_btns = [b for b in at4.sidebar.button
                  if str(b.key).startswith("open_")]
    if _open_btns:
        _btn = _open_btns[-1]
        _want = int(str(_btn.key).split("_")[1])
        _btn.click().run()
        _got = (at4.session_state["selected_id"]
                if "selected_id" in at4.session_state else None)
        check("点击卡片后 selected_id = 对应询盘",
              _got == _want, "%s vs %s" % (_got, _want))
    else:
        check("整卡覆盖按钮存在", False, "no open_ buttons")
else:
    print("  ⏭️ 库中无询盘数据，跳过选中测试")

# ---------- Test A：同客户多条往来聚合（第二十二轮 Deal Threading）----------
print("== Test A：同客户多消息聚合为一张 Deal 卡 ==")
if _same_company:
    _ids = sorted(r[0] for r in _rows if (r[5] or r[6]) == _same_company)
    at5 = AppTest.from_file(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "workbench", "app.py"), default_timeout=60)
    at5.run()
    # 第二十二轮：默认只渲染「每 Deal 一张主卡」+ 展开按钮（sd_），不再
    # 把同客户同产品的每条消息都渲染成一张独立卡。
    _sdkeys = [str(b.key) for b in at5.sidebar.button
               if str(b.key).startswith("sd_")]
    check("多消息聚合后有「展开历史往来」按钮", len(_sdkeys) >= 1,
          str(_sdkeys))
    for _k in _sdkeys:
        _b = at5.sidebar.button(key=_k)
        if _b:
            _b.click().run()
    md5 = "\n".join(x.value for x in at5.sidebar.markdown)
    check("展开后该客户全部消息 #ID 仍可见（历史不丢）",
          all(("#%d" % i) in md5 for i in _ids), str(_ids))
else:
    print("  ⏭️ 库中暂无同客户多条询盘，跳过")

print()
print(f"结果：{_PASS} 通过 / {_FAIL} 失败")
sys.exit(1 if _FAIL else 0)
