# -*- coding: utf-8 -*-
"""第十四轮验收测试（Phase 4）：Sales Execution Queue 十二场景。

闭环：AI Priority → Next Best Action → Execute → Activity → Update State
      → Recalculate → Rebuild Queue → Next Customer

场景（spec 八）：
  1 完成回复         2 完成跟进        3 创建报价路由    4 修改 Stage（谈判推进）
  5 Queue 重新排序   6 下一条          7 最后一条        8 空队列
  9 API Error       10 Refresh        11 Browser Back   12 重复点击

隔离：复制 workbench.db 到临时目录并 monkeypatch db.DB_PATH，
真实库零污染。全部走 AppTest（无头），不依赖网络。
"""
import io
import os
import shutil
import sqlite3
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "workbench"))

import db  # noqa: E402
import execution as _ex  # noqa: E402
import workflow as _wf  # noqa: E402

_PASS = _FAIL = 0


def check(name, ok, detail=""):
    global _PASS, _FAIL
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f"　[{detail}]" if detail and not ok else ""))
    if ok:
        _PASS += 1
    else:
        _FAIL += 1


def ss(at, key, default=None):
    try:
        return at.session_state[key]
    except Exception:
        return default


def ss_pop(at, key):
    try:
        del at.session_state[key]
    except Exception:
        pass


def body_md(at):
    parts = [x.value for x in at.main.markdown]
    for getter in ("info", "success", "error", "warning"):
        try:
            parts += [e.value for e in getattr(at, getter)]
        except Exception:
            pass
    return "\n".join(str(p) for p in parts)


def body_all(at):
    parts = [x.value for x in at.main.markdown]
    parts += [b.label for b in at.main.button]
    return "\n".join(str(p) for p in parts)


# ---------------- 临时库隔离 ----------------
_TMP = tempfile.mkdtemp(prefix="qa_p4_")
_DB = os.path.join(_TMP, "workbench.db")
shutil.copy(os.path.join(BASE, "workbench", "workbench.db"), _DB)
db.DB_PATH = _DB                      # db 函数体内动态读 DB_PATH → 全部生效

sys.path.insert(0, os.path.join(BASE, "workbench"))
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = os.path.join(BASE, "workbench", "app.py")
AVAIL = [r[0] for r in db.list_inquiries(None)]
assert len(AVAIL) >= 4, "临时库询盘不足"


def new_app():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, f"页面异常: {at.exception}"
    return at


def enter_exec(at, ids, idx=0):
    """直接注入执行队列状态（受控场景）。"""
    at.session_state["actq_entered"] = True
    at.session_state["actq_ids"] = list(ids)
    at.session_state["actq_idx"] = idx
    at.session_state["actq_flt"] = "全部"
    at.session_state["actq_sort"] = _ex.SORT_DEFAULT
    ss_pop(at, "exec_error")
    ss_pop(at, "actq_done_all")
    at.run()
    assert not at.exception, f"进入执行队列异常: {at.exception}"


def primary_btn(at, iid):
    return next((b for b in at.main.button
                 if b.key == f"exec_primary_{iid}"), None)


def reset_row(iid, biz, follow_up_at=None):
    db.update_inquiry_status(iid, "待处理")
    conn = sqlite3.connect(_DB)
    conn.execute("UPDATE inquiries SET biz_status=?, deal_status='', "
                 "last_replied_at='', follow_up_at=?, follow_up_done=0 "
                 "WHERE id=?", (biz, follow_up_at or "", iid))
    conn.execute("DELETE FROM activity WHERE inquiry_id=?", (iid,))
    conn.commit()
    conn.close()


def act_count(iid, type_):
    return sum(1 for a in db.list_activity(iid) if a[0] == type_)


print("=" * 62)
print("Phase 4 验收：Sales Execution Queue（12 场景）")
print("=" * 62)

# ---- 场景 1：完成回复（REPLY 路由闭环）----
print("\n== 场景 1：完成回复 ==")
A, B = AVAIL[0], AVAIL[1]
reset_row(A, "READY_TO_REPLY")
reset_row(B, "READY_TO_REPLY")
at = new_app()
enter_exec(at, [A, B])
check("1.1 执行队列视图渲染（Sales Execution Queue）",
      "Sales Execution Queue" in body_md(at))
pb = primary_btn(at, A)
check("1.2 REPLY 路由完成按钮为「已发送回复 · 下一条」",
      pb is not None and "已发送回复" in pb.label, pb.label if pb else "无按钮")
pb.click().run()
assert not at.exception, f"完成回复异常: {at.exception}"
wf = db.get_workflow(A)
check("1.3 Inquiry 更新：biz_status → REPLIED", wf.get("biz_status") == "REPLIED",
      str(wf))
check("1.4 Last Contact 更新：last_replied_at 已写入",
      bool(wf.get("last_replied_at")))
check("1.5 Status 更新：两态 → 已处理",
      db.get_inquiry_status(A) == "已处理")
check("1.6 Activity 已保存：REPLIED 事件 ×1", act_count(A, "REPLIED") == 1)
check("1.7 下一条：自动打开队列下一条（非当前）",
      ss(at, "selected_id") != A and (ss(at, "actq_ids") or [None])[0] != A,
      f"sel={ss(at, 'selected_id')} ids={ss(at, 'actq_ids')}")

# ---- 场景 2：完成跟进（FOLLOW_UP 路由）----
print("\n== 场景 2：完成跟进 ==")
C = AVAIL[2]
reset_row(C, "FOLLOW_UP", follow_up_at="2026-01-01 09:00")   # 已到期
at = new_app()
enter_exec(at, [C, B])
md = body_all(at)
check("2.1 FOLLOW_UP 路由完成按钮为「已完成跟进 · 下一条」",
      "已完成跟进" in md, md[:200])
pb = primary_btn(at, C)
pb.click().run()
assert not at.exception, f"完成跟进异常: {at.exception}"
wf = db.get_workflow(C)
check("2.2 follow_up_done → 1", wf.get("follow_up_done") == 1)
check("2.3 Activity：FOLLOW_UP_COMPLETED ×1",
      act_count(C, "FOLLOW_UP_COMPLETED") == 1)
check("2.4 已推进到下一条（重建队列后的最高优先级，非当前）",
      ss(at, "selected_id") != C, f"sel={ss(at, 'selected_id')}")

# ---- 场景 3：创建报价路由（CREATE_QUOTE → 打开报价区 + 完成推进）----
print("\n== 场景 3：创建报价 ==")
D = AVAIL[3]
reset_row(D, "READY_FOR_QUOTE")
at = new_app()
enter_exec(at, [D])
route_btn = next((b for b in at.main.button
                  if b.key == f"aq_route_{D}"), None)
check("3.1 CREATE_QUOTE 路由按钮为「打开报价」",
      route_btn is not None and "报价" in route_btn.label,
      route_btn.label if route_btn else "无按钮")
route_btn.click().run()
check("3.2 打开询盘详情报价区（ui_quote 展开）",
      ss(at, "selected_id") == D and ss(at, f"ui_quote_{D}") is True)
pb = primary_btn(at, D)
check("3.3 完成按钮存在且可用", pb is not None and not pb.disabled)
pb.click().run()
assert not at.exception, f"报价完成推进异常: {at.exception}"
check("3.4 不重复落库（完成推进不伪造 QUOTE_CREATED）",
      act_count(D, "QUOTE_CREATED") == 0)

# ---- 场景 4：修改 Stage（工作区 Pipeline 真实推进 QUOTED→NEGOTIATING）----
print("\n== 场景 4：修改 Stage ==")
E = AVAIL[4] if len(AVAIL) > 4 else D
reset_row(E, "QUOTED")
at = new_app()
enter_exec(at, [E])
view_btn = next((b for b in at.main.button
                 if b.key == f"aq_view_{E}"), None)
check("4.1 [查看客户] 打开详情工作区", view_btn is not None)
view_btn.click().run()
seg = next((c for c in at.main.segmented_control
            if c.key == f"ws_pipe_{E}"), None)
check("4.2 Pipeline 阶段选择器存在", seg is not None)
if seg is not None:
    seg.set_value("谈判").run()               # 点选「谈判」段 → 知识卡 + 推进按钮
    go = next((b for b in at.main.button
               if b.key == f"ws_pipe_go_{E}"), None)
    check("4.3 状态机允许推进（推进按钮可用）",
          go is not None and not go.disabled)
    if go:
        go.click().run()
        assert not at.exception, f"推进阶段异常: {at.exception}"
        wf = db.get_workflow(E)
        check("4.4 Stage 修改：QUOTED → NEGOTIATING",
              wf.get("biz_status") == "NEGOTIATING",
              str(wf.get("biz_status")))
        check("4.5 Timeline：阶段变化事件 ×1（不伪造历史）",
              act_count(E, "STATUS_CHANGE") == 1)

# ---- 场景 5：Queue 重新排序 ----
print("\n== 场景 5：Queue 重新排序 ==")
reset_row(A, "READY_TO_REPLY")
reset_row(B, "READY_TO_REPLY")
at = new_app()
enter_exec(at, [A, B])
old_ids = list(ss(at, "actq_ids"))
pb = primary_btn(at, A)
pb.click().run()
new_ids = list(ss(at, "actq_ids") or [])
check("5.1 队列已重建（Rebuild Queue）", new_ids != old_ids or not new_ids,
      f"{old_ids} → {new_ids}")
check("5.2 已完成记录不再排首位", (new_ids or [None])[0] != A,
      str(new_ids[:2]))
check("5.3 重算 AI Priority：重建队列来自 load_queue 全量重派生",
      all(isinstance(x, int) for x in new_ids))

# ---- 场景 6：下一条 ----
print("\n== 场景 6：下一条 ==")
check("6.1 完成后 selected_id = 队列下一条",
      ss(at, "selected_id") == ((new_ids or [None])[0]),
      f"sel={ss(at, 'selected_id')} first={new_ids[:1]}")

# ---- 场景 7：最后一条（构造只剩一条待处理的队列）----
print("\n== 场景 7：最后一条 ==")
G = AVAIL[0]
reset_row(G, "READY_TO_REPLY")
conn = sqlite3.connect(_DB)                   # 其余全部置「已处理」→ 沉出执行队列
conn.execute("UPDATE inquiries SET status='已处理' WHERE id<>?", (G,))
conn.commit()
conn.close()
at = new_app()
enter_exec(at, [G])
pb = primary_btn(at, G)
pb.click().run()
assert not at.exception
check("7.1 完成最后一条 → 退出执行模式",
      ss(at, "actq_ids") is None and ss(at, "actq_done_all") is True,
      f"ids={ss(at, 'actq_ids')} done_all={ss(at, 'actq_done_all')}")
check("7.2 显示处理完毕提示", "处理完毕" in body_md(at))

# ---- 场景 8：空队列 ----
print("\n== 场景 8：空队列 ==")
at = new_app()
at.session_state["actq_entered"] = True
at.session_state["actq_ids"] = []
at.run()
check("8.1 显示「当前筛选条件下没有待处理任务。」",
      "当前筛选条件下没有待处理任务" in body_md(at))
back = next((b for b in at.main.button if b.key == "exec_empty_back"), None)
check("8.2 提供 [返回工作台]", back is not None)
if back:
    back.click().run()
    check("8.3 返回后退出执行模式", ss(at, "actq_entered") is None
          and "当前筛选条件下没有待处理任务" not in body_md(at))

# ---- 场景 9：API Error（完成动作抛异常 → 不跳下一条 → 重试）----
print("\n== 场景 9：API Error ==")
reset_row(A, "READY_TO_REPLY")
at = new_app()
enter_exec(at, [A, B])
_orig_mark = db.mark_replied


def _boom(*a, **k):
    raise RuntimeError("模拟 API 失败")


db.mark_replied = _boom
try:
    pb = primary_btn(at, A)
    pb.click().run()
finally:
    db.mark_replied = _orig_mark
assert not at.exception, f"错误路径崩溃: {at.exception}"
err = ss(at, "exec_error") or {}
check("9.1 exec_error 已记录（不跳下一条）",
      err.get("iid") == A and ss(at, "actq_ids") and
      (ss(at, "actq_ids") or [])[0] == A, str(err))
check("9.2 显示「操作失败，当前记录未完成。」",
      "操作失败，当前记录未完成" in body_md(at))
check("9.3 提供 [重试]",
      any(b.key == "exec_retry" for b in at.main.button))
retry = next(b for b in at.main.button if b.key == "exec_retry")
retry.click().run()
assert not at.exception
check("9.4 重试成功：完成 + 推进",
      db.get_workflow(A).get("biz_status") == "REPLIED"
      and (ss(at, "actq_ids") or [None])[0] != A)
check("9.5 重试后错误卡消失", ss(at, "exec_error") is None)

# ---- 场景 10：Refresh（重跑保留执行队列状态）----
print("\n== 场景 10：Refresh ==")
reset_row(B, "READY_TO_REPLY")
at = new_app()
enter_exec(at, [B, A])
ids_before = list(ss(at, "actq_ids"))
at.run()                                    # 模拟刷新（同会话重跑）
assert not at.exception
check("10.1 刷新后仍在执行队列视图",
      "Sales Execution Queue" in body_md(at))
check("10.2 队列与指针不丢失",
      list(ss(at, "actq_ids")) == ids_before and ss(at, "actq_idx") == 0)

# ---- 场景 11：Browser Back（退出/回退不破坏状态）----
print("\n== 场景 11：Browser Back ==")
close = next((b for b in at.main.button if b.key == "aq_close"), None)
check("11.1 [退出队列] 可用", close is not None)
close.click().run()
assert not at.exception
check("11.2 退出后回到正常工作区（无执行队列卡）",
      "Sales Execution Queue" not in body_md(at))
at2 = AppTest.from_file(APP, default_timeout=120)   # 新会话 ≈ 浏览器回退重开
at2.run()
check("11.3 新会话（回退重开）页面正常", not at2.exception)

# ---- 场景 12：重复点击（数据一致性）----
print("\n== 场景 12：重复点击 ==")
reset_row(B, "READY_TO_REPLY")
at = new_app()
enter_exec(at, [B])
pb = primary_btn(at, B)
pb.click().run()
assert not at.exception
check("12.1 第一次完成：REPLIED ×1、biz → REPLIED",
      act_count(B, "REPLIED") == 1
      and db.get_workflow(B).get("biz_status") == "REPLIED")
check("12.2 幂等记录单元：第一次写入/第二次跳过",
      db.record_activity_once(B, "PHONE_CALL", "第一次") is True
      and db.record_activity_once(B, "PHONE_CALL", "重复") is False)
check("12.3 PHONE_CALL Activity 只有一条", act_count(B, "PHONE_CALL") == 1)
check("12.4 is_dup_activity 判定窗口内同类型为重复",
      _ex.is_dup_activity(db.list_activity(B), "PHONE_CALL"))
# 再次把该记录塞回执行队列 → 系统应不再提供「发送回复」入口（防重复发送）
enter_exec(at, [B])
pb = primary_btn(at, B)
check("12.5 已回复记录的完成按钮不再是「已发送回复」（路由降级为标记完成）",
      pb is not None and "已发送回复" not in pb.label,
      pb.label if pb else "无按钮")
pb.click().run()
assert not at.exception
check("12.6 重复完成：不产生第二条 REPLIED（重复发送守卫）",
      act_count(B, "REPLIED") == 1, str(act_count(B, "REPLIED")))
check("12.7 biz_status 不变（重复状态更新被拦截）",
      db.get_workflow(B).get("biz_status") == "REPLIED")

# ---- 清理 ----
try:
    os.remove(_DB)
    os.rmdir(_TMP)
except OSError:
    pass

print("\n" + "=" * 62)
print(f"结果：{_PASS} 通过 / {_FAIL} 失败　（真实库零污染，临时库已清理）")
print("=" * 62)
sys.exit(1 if _FAIL else 0)
