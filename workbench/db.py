# -*- coding: utf-8 -*-
"""
工作台数据库层（SQLite，Python 标准库，零依赖）
负责把每次询盘分析结果持久化，让工作台"有记忆"。

表：
  inquiries  - 每条询盘原文 + 完整报告(JSON) + 常用检索字段
  customers  - 由询盘自动归并出的客户档案（按邮箱/公司去重）
"""
import os
import json
import sqlite3
import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench.db")

# 模块版本标记：app.py 的自愈式 reload 靠它判断内存里的 db 模块是否过期。
# 每轮给 db.py 加新列/新函数时，同步升级这里的标记名。
R10_CONTACTS = True
# Phase 2（第十二轮）：activity 增加 result 列（活动结果），并新增 update_draft 草稿保存
R12_ACT_RESULT = True
# Phase 3（第十三轮）：_derive_need 增加只读派生字段 product_cat / lead_summary，
# 供 AI Priority 七维评分与 Queue Score 使用（不改任何业务数据）。
R13_LEAD_SUMMARY = True

# 询盘处理状态
STATUS_TODO = "待处理"
STATUS_DONE = "已处理"

# 优先级：按客户等级自动排队
GRADE_POINT = {"A": 50, "B": 35, "C": 20, "D": 5}
PRI_HIGH, PRI_MID, PRI_LOW, PRI_DONE = "🔴 优先处理", "🟠 正常处理", "🟢 可延后", "⚪ 已完成"


def calc_priority(cust_grade, lead_grade, score, urgency, status):
    """算处理优先级，返回 (优先级名称, 分值)。

    分值 = 客户等级分 + 线索等级分×0.6 + 评分×0.1 + 紧急度加分(高15/中7)
    已处理的一律归为"已完成"，分值为 0，不参与排队。
    """
    if status == STATUS_DONE:
        return PRI_DONE, 0
    p = 0
    p += GRADE_POINT.get(cust_grade or lead_grade or "", 0)   # 客户等级（手动归档优先）
    p += int(GRADE_POINT.get(lead_grade or "", 0) * 0.6)      # 线索等级
    p += int((score or 0) * 0.1)                              # 评分
    p += {"high": 15, "medium": 7}.get(urgency or "", 0)      # 紧急度
    # 第四版优化一：有明确时间要求（紧急）的询盘不允许掉进“可延后”队列
    if urgency == "high" and p < 40:
        p = 40
    if p >= 65:
        return PRI_HIGH, p
    if p >= 35:
        return PRI_MID, p
    return PRI_LOW, p


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def init_db():
    """首次运行时建表（已存在则忽略），并为旧库补列。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS inquiries (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at   TEXT NOT NULL,
        source_text  TEXT NOT NULL,
        report_json  TEXT NOT NULL,
        grade        TEXT,
        score        INTEGER,
        country      TEXT,
        company      TEXT,
        contact_name TEXT,
        customer_id  INTEGER
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        ckey           TEXT UNIQUE NOT NULL,
        company        TEXT,
        country        TEXT,
        email          TEXT,
        website        TEXT,
        contact_name   TEXT,
        inquiry_count  INTEGER DEFAULT 1,
        last_grade     TEXT,
        last_score     INTEGER,
        first_seen     TEXT,
        last_seen      TEXT
    )
    """)
    # 兼容旧库：没有 customer_id 列时补上
    try:
        c.execute("ALTER TABLE inquiries ADD COLUMN customer_id INTEGER")
    except sqlite3.OperationalError:
        pass
    # 兼容旧库：备注列
    try:
        c.execute("ALTER TABLE inquiries ADD COLUMN note TEXT")
    except sqlite3.OperationalError:
        pass
    # 兼容旧库：处理状态（待处理 / 已处理）
    try:
        c.execute("ALTER TABLE inquiries ADD COLUMN status TEXT DEFAULT '待处理'")
    except sqlite3.OperationalError:
        pass
    # 兼容旧库：客户档案的等级(手动归类)与备注
    try:
        c.execute("ALTER TABLE customers ADD COLUMN grade TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE customers ADD COLUMN note TEXT")
    except sqlite3.OperationalError:
        pass
    # 第十轮（spec 三）：轻量联系人层——JSON 数组 [{"name","seen"}]，
    # 同一客户档案可挂多个联系人；只追加，绝不覆盖已有联系人
    try:
        c.execute("ALTER TABLE customers ADD COLUMN contacts TEXT")
    except sqlite3.OperationalError:
        pass
    # —— 第七轮：销售执行闭环 ——
    # 业务状态（12 态英文常量，见 workflow.py）；NULL = 旧记录，UI 从现有数据派生
    for col, ddl in (
            ("biz_status", "TEXT"),
            ("last_replied_at", "TEXT"),
            ("follow_up_at", "TEXT"),
            ("follow_up_done", "INTEGER DEFAULT 0"),
            ("deal_status", "TEXT"),
    ):
        try:
            c.execute(f"ALTER TABLE inquiries ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass
    # 业务事件记录（Activity / Timeline，spec 十七）：只记录，不参与分析
    c.execute("""
    CREATE TABLE IF NOT EXISTS activity (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        inquiry_id  INTEGER NOT NULL,
        type        TEXT NOT NULL,
        ts          TEXT NOT NULL,
        description TEXT,
        actor       TEXT DEFAULT '销售'
    )
    """)
    # Phase 2：活动结果列（如「草稿已保存 / 已推进到已报价」）；旧库幂等补列
    try:
        c.execute("ALTER TABLE activity ADD COLUMN result TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def _customer_key(info: dict):
    """用邮箱（优先）或公司名生成去重键；都没有则返回 None（不归并）。"""
    email = (info.get("email") or "").strip().lower()
    if email and "@" in email:
        return "email:" + email
    company = (info.get("company") or "").strip().lower()
    if company:
        return "company:" + company
    return None


def _merge_contacts(existing_json, name, seen):
    """往联系人 JSON 数组里追加一个新联系人（同名不重复追加，只加不覆盖）。"""
    try:
        arr = json.loads(existing_json or "[]")
        if not isinstance(arr, list):
            arr = []
    except Exception:
        arr = []
    n = str(name or "").strip()
    if not n:
        return json.dumps(arr, ensure_ascii=False) if arr else (existing_json or "[]")
    if not any(isinstance(c, dict)
               and str(c.get("name", "")).strip().lower() == n.lower()
               for c in arr):
        arr.append({"name": n, "seen": seen})
    return json.dumps(arr, ensure_ascii=False)


def upsert_customer(info: dict, grade=None, score=None) -> int | None:
    """根据询盘里的客户信息归并到客户档案，返回 customer_id。"""
    key = _customer_key(info)
    if not key:
        return None
    now = _now()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, inquiry_count, contacts FROM customers WHERE ckey=?", (key,)
    )
    row = c.fetchone()
    new_contacts = _merge_contacts(None, info.get("contact_name"), now)
    if row:
        cid = row[0]
        merged = _merge_contacts(row[2], info.get("contact_name"), now)
        c.execute(
            """UPDATE customers SET
                   company=COALESCE(?, company),
                   country=COALESCE(?, country),
                   email=COALESCE(?, email),
                   website=COALESCE(?, website),
                   contact_name=COALESCE(?, contact_name),
                   inquiry_count=inquiry_count+1,
                   last_grade=?, last_score=?, last_seen=?, contacts=?
               WHERE id=?""",
            (
                info.get("company") or None,
                info.get("country") or None,
                info.get("email") or None,
                info.get("website") or None,
                info.get("contact_name") or None,
                grade,
                score,
                now,
                merged,
                cid,
            ),
        )
    else:
        c.execute(
            """INSERT INTO customers
               (ckey, company, country, email, website, contact_name,
                inquiry_count, last_grade, last_score, grade, first_seen, last_seen,
                contacts)
               VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?)""",
            (
                key,
                info.get("company") or None,
                info.get("country") or None,
                info.get("email") or None,
                info.get("website") or None,
                info.get("contact_name") or None,
                grade,
                score,
                grade,
                now,
                now,
                new_contacts,
            ),
        )
        cid = c.lastrowid
    conn.commit()
    conn.close()
    return cid


def save_inquiry(source_text: str, report: dict) -> int:
    """保存一条分析结果（并归并客户），返回新记录 id。"""
    info = report.get("extracted", {})
    lead = report.get("lead", {})
    cust_id = upsert_customer(info, lead.get("grade"), lead.get("score"))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """INSERT INTO inquiries
           (created_at, source_text, report_json, grade, score, country,
            company, contact_name, customer_id, status)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            _now(),
            source_text,
            json.dumps(report, ensure_ascii=False),
            lead.get("grade"),
            lead.get("score"),
            info.get("country"),
            info.get("company"),
            info.get("contact_name"),
            cust_id,
            "待处理",
        ),
    )
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    # 第十轮：AI 分析事件在入库点统一记录（Timeline 保证每条询盘有起点事件）
    # Phase 2：执行人规范为 AI（历史旧记录 actor=销售，由展示层对 ANALYZED/
    # REPLY_GENERATED 固定显示 AI，不改历史）
    record_activity(new_id, "ANALYZED", "AI 完成询盘分析", actor="AI")
    if (report.get("draft") or "").strip():
        record_activity(new_id, "REPLY_GENERATED", "生成客户回复草稿", actor="AI")
    return new_id


def _lead_summary(rep: dict) -> dict:
    """从 report 的 lead（LeadScorer 结果）提取 AI Priority 所需的只读摘要。

    Phase 3（第十三轮）：只读派生，不改任何业务数据。兼容两种 lead 结构：
      - v4 顶层标量（purchase_intent_score / urgency_score / …）；
      - 旧版 dims 数组（{key, name, score, …}）。
    取不到的分值一律 None（由上层按「暂无数据」处理，绝不猜数）。
    """
    lead = rep.get("lead") or {}
    if not isinstance(lead, dict):
        return {}
    dmap = {}
    for d in (lead.get("dims") or []):
        if isinstance(d, dict) and d.get("key"):
            s = d.get("score")
            if isinstance(s, (int, float)):
                dmap[d["key"]] = s

    def _g(topkey, dimkey):
        v = lead.get(topkey)
        return v if isinstance(v, (int, float)) else dmap.get(dimkey)

    urg = str(lead.get("urgency") or "")
    return {
        "intent": _g("purchase_intent_score", "intent"),
        "volume": _g("order_volume_score", "volume"),
        "clarity": _g("requirement_clarity_score", "clarity"),
        "quality": _g("customer_quality_score", "quality"),
        "match": _g("product_match_score", "product_match"),
        "conversion": _g("conversion_probability_score", "conversion"),
        "maturity": _g("business_maturity_score", "business_maturity"),
        "urgency": urg,                                     # high/medium/low/空
        "urgency_score": _g("urgency_score", "urgency"),
        "quality_total": lead.get("customer_quality_total")
        if isinstance(lead.get("customer_quality_total"), (int, float)) else None,
        "overall": lead.get("score")
        if isinstance(lead.get("score"), (int, float)) else None,
        "has_dims": bool(lead.get("dims")),
    }


def _derive_need(rep: dict) -> dict:
    """UI 展示用的派生信息（只读解析 report_json，不改任何业务数据）。

    供「销售 Inbox」「AI 今日建议」「KPI」展示：
      intent     采购意向一句话
      qty        采购数量文本（如「约 3,000 pcs」）
      blockers   阻塞项字段名列表（来自报价准备度 blockers_preliminary）
      readiness  报价准备度状态原文
    Phase 3 追加（只读）：
      product_cat   客户已明确的产品品类（extracted.product_category.value）
      lead_summary  LeadScorer 关键分值摘要（AI Priority 输入）
    """
    info = rep.get("extracted") or {}
    qr = ((rep.get("insight") or {}).get("quotation_readiness") or {})
    qty = info.get("quantity")
    # 第十轮（spec 十五）：搜索需要产品维度——取产品匹配第一条的名称（只读派生）
    _m = rep.get("matches") or []
    product = ""
    match_top = {}
    if _m and isinstance(_m[0], dict):
        product = str(_m[0].get("name_cn") or _m[0].get("name") or "").strip()
        _s0 = _m[0].get("match_score")
        match_top = {"name": product,
                     "score": float(_s0) if isinstance(_s0, (int, float)) else None}
    # Phase 3：客户已明确的产品品类（只读；用于产品匹配缺数据判定，不猜数）
    _pc = info.get("product_category")
    product_cat = ""
    if isinstance(_pc, dict):
        product_cat = str(_pc.get("value") or "").strip()
    elif isinstance(_pc, str):
        product_cat = _pc.strip()
    try:
        qty_txt = (f"约 {int(qty):,} {(info.get('quantity_unit') or '').strip()}".strip()
                   if qty else "")
    except Exception:
        qty_txt = str(qty or "")
    # 第十轮（spec 十七）：商机金额估算用的原始数值（只读派生，不改业务数据）；
    # 解析失败就是 None，绝不编造
    try:
        price_num = float(str(info.get("target_price")).replace(",", "")) \
            if info.get("target_price") else None
    except Exception:
        price_num = None
    return {
        "intent": info.get("intent") or "",
        "qty": qty_txt,
        "product": product,
        "product_cat": product_cat,
        "match_top": match_top,
        "lead_summary": _lead_summary(rep),
        "has_draft": bool((rep.get("draft") or "").strip()),
        "qty_num": qty if isinstance(qty, (int, float)) else None,
        "price_num": price_num,
        "target_price_currency": info.get("target_price_currency") or "",
        "blockers": [str(b.get("field", "")) for b in (qr.get("blockers_preliminary") or [])],
        "readiness": qr.get("quotation_readiness_status") or "",
    }


def list_inquiries(status=None):
    """按时间倒序列出历史（用于侧边栏 / 跟进台）。

    status: None=全部 / '待处理' / '已处理'
    返回每行：
      (id, created_at, grade, score, country, company, contact_name,
       status, cust_grade, cust_last_grade, urgency, need_dict)
      need_dict 为 UI 展示派生字段（intent/qty/blockers/readiness），只读
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    sql = """
        SELECT i.id, i.created_at, i.grade, i.score, i.country, i.company,
               i.contact_name,
               COALESCE(i.status, '待处理') AS status,
               c.grade, c.last_grade, i.report_json, i.customer_id,
               i.biz_status, i.last_replied_at, i.follow_up_at,
               i.follow_up_done, i.deal_status
        FROM inquiries i LEFT JOIN customers c ON i.customer_id = c.id
    """
    if status:
        sql += " WHERE COALESCE(i.status,'待处理')=?"
        c.execute(sql + " ORDER BY i.id DESC", (status,))
    else:
        c.execute(sql + " ORDER BY i.id DESC")
    rows = c.fetchall()
    conn.close()
    out = []
    for r in rows:
        urgency = None
        need = {}
        try:
            rep = json.loads(r[10] or "{}")
            urgency = (rep.get("extracted") or {}).get("urgency")
            need = _derive_need(rep)
        except Exception:
            urgency, need = None, {}
        # 追加列只放末尾：r[:10] + (urgency, need) 的既有位置不变（第 12 位起为新增），
        # 旧版消费方按 row[:11] + row[11] 取值不受影响；customer_id 供 UI 客户聚合用
        cust_id = r[11] if len(r) > 11 else None
        # 第七轮：闭环字段（旧库无列时全空 → UI 从现有数据派生）
        wf = {}
        if len(r) > 16:
            wf = {"biz_status": r[12] or "", "last_replied_at": r[13] or "",
                  "follow_up_at": r[14] or "", "follow_up_done": r[15] or 0,
                  "deal_status": r[16] or ""}
        out.append(r[:10] + (urgency, need, cust_id, wf))
    return out


def update_inquiry_status(id_: int, status: str):
    """标记某条询盘为 待处理 / 已处理。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE inquiries SET status=? WHERE id=?", (status, id_))
    conn.commit()
    conn.close()


def get_inquiry_status(id_: int) -> str:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(status,'待处理') FROM inquiries WHERE id=?", (id_,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else "待处理"


def get_inquiry_priority_inputs(id_):
    """取计算优先级所需的输入：(status, 线索等级, 分数, 客户等级, 紧急度)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """SELECT COALESCE(i.status,'待处理'), i.grade, i.score, c.grade, i.report_json
           FROM inquiries i LEFT JOIN customers c ON i.customer_id=c.id
           WHERE i.id=?""",
        (id_,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return STATUS_TODO, None, 0, None, None
    urgency = None
    try:
        urgency = (json.loads(row[4] or "{}").get("extracted") or {}).get("urgency")
    except Exception:
        urgency = None
    return row[0], row[1], row[2], row[3], urgency


def count_by_status():
    """返回 (待处理数, 已处理数)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(status,'待处理'), COUNT(*) FROM inquiries GROUP BY 1")
    d = dict(c.fetchall())
    conn.close()
    return d.get("待处理", 0), d.get("已处理", 0)


def get_inquiry(id_: int):
    """取某条记录的原文和完整报告"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT source_text, report_json FROM inquiries WHERE id=?", (id_,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0], json.loads(row[1])
    return None, None


# ---------------- 销售执行闭环（第七轮） ----------------
def record_activity(inquiry_id: int, type_: str, description: str = "",
                    actor: str = "销售", result: str = ""):
    """记录一条业务事件（ANALYZED / REPLY_GENERATED / REPLIED /
    FOLLOW_UP_CREATED / FOLLOW_UP_COMPLETED / WON / LOST / PHONE_CALL …
    Phase 2 起支持 result（活动结果），默认空串，不改旧语义）。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO activity (inquiry_id, type, ts, description, actor, result) "
              "VALUES (?,?,?,?,?,?)",
              (inquiry_id, type_, _now(), description, actor, result))
    conn.commit()
    conn.close()


def list_activity(inquiry_id: int):
    """某条询盘的业务事件（按时间正序 = 业务发生顺序）。

    Phase 2 起返回 5 元组 (type, ts, description, actor, result)，
    旧 4 元组消费方需同步解包；result 为空串表示无记录。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT type, ts, description, actor, COALESCE(result, '') "
              "FROM activity WHERE inquiry_id=? ORDER BY id ASC", (inquiry_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def record_activity_once(inquiry_id: int, type_: str, description: str = "",
                         actor: str = "销售", result: str = "",
                         window_min: int = 2) -> bool:
    """幂等版 record_activity（Phase 4 数据一致性）：
    同一询盘在 window_min 分钟内已有同类型事件 → 视为重复点击，跳过写入。
    返回 True=新写入，False=去重跳过。防「重复 Activity / 重复状态更新」。"""
    try:
        recent = list_activity(inquiry_id)
    except Exception:
        recent = []
    cut = (datetime.datetime.now()
           - datetime.timedelta(minutes=window_min)).strftime("%Y-%m-%d %H:%M")
    for t, ts, *_ in recent[-10:]:
        if t == type_ and str(ts or "") >= cut:
            return False
    record_activity(inquiry_id, type_, description, actor, result)
    return True


def update_draft(inquiry_id: int, draft_text: str) -> bool:
    """保存/更新某条询盘的回复草稿（写入 report_json.draft）。

    只改草稿字段，不动分析结论 / 状态 / 事件。调用方负责记录 REPLY_EDITED。
    返回是否成功（询盘不存在返回 False）。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT report_json FROM inquiries WHERE id=?", (inquiry_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    try:
        rep = json.loads(row[0])
    except Exception:
        rep = {}
    rep["draft"] = draft_text or ""
    c.execute("UPDATE inquiries SET report_json=? WHERE id=?",
              (json.dumps(rep, ensure_ascii=False), inquiry_id))
    conn.commit()
    conn.close()
    return True


def get_workflow(inquiry_id: int) -> dict:
    """取一条询盘的闭环字段：业务状态 / 最后回复时间 / 跟进时间 / 成交丢单。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT COALESCE(biz_status,''), COALESCE(last_replied_at,''),
                        COALESCE(follow_up_at,''),
                        COALESCE(follow_up_done,0), COALESCE(deal_status,'')
                 FROM inquiries WHERE id=?""", (inquiry_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return {"biz_status": "", "last_replied_at": "",
                "follow_up_at": "", "follow_up_done": 0, "deal_status": ""}
    return {"biz_status": row[0], "last_replied_at": row[1],
            "follow_up_at": row[2], "follow_up_done": row[3],
            "deal_status": row[4]}


def mark_replied(inquiry_id: int, when: str | None = None):
    """「标记为已发送」（CRM 状态操作，不发送真实邮件）：
    业务状态 → REPLIED，记录 last_replied_at 与 REPLIED 事件。
    两态 status 同步为「已处理」（退出待回复队列，兼容既有筛选口径）。"""
    ts = when or _now()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""UPDATE inquiries SET biz_status='REPLIED', last_replied_at=?,
                 follow_up_done=0, status='已处理' WHERE id=?""", (ts, inquiry_id))
    conn.commit()
    conn.close()
    record_activity(inquiry_id, "REPLIED", "标记为已发送（人工确认）")


def set_follow_up(inquiry_id: int, at_str: str):
    """设置跟进时间（24小时/48小时/3天/7天/自定义），并记录 FOLLOW_UP_CREATED。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE inquiries SET follow_up_at=?, follow_up_done=0 WHERE id=?",
              (at_str, inquiry_id))
    conn.commit()
    conn.close()
    record_activity(inquiry_id, "FOLLOW_UP_CREATED", f"计划跟进：{at_str}")


def complete_follow_up(inquiry_id: int):
    """完成一次跟进。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE inquiries SET follow_up_done=1 WHERE id=?", (inquiry_id,))
    conn.commit()
    conn.close()
    record_activity(inquiry_id, "FOLLOW_UP_COMPLETED", "跟进完成")


def set_deal(inquiry_id: int, result: str):
    """标记成交 / 丢单（必须由人工在 UI 确认后调用）。
    result: 'WON' / 'LOST'。"""
    assert result in ("WON", "LOST"), "deal_status 只能是 WON / LOST"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE inquiries SET biz_status=?, deal_status=? WHERE id=?",
              (result, result, inquiry_id))
    conn.commit()
    conn.close()
    record_activity(inquiry_id, result,
                    "标记成交" if result == "WON" else "标记丢单")


def update_biz_status(inquiry_id: int, biz_status: str):
    """直接写业务状态（供状态机推进；转换合法性由 UI 层 workflow.can_transition 校验）。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE inquiries SET biz_status=? WHERE id=?", (biz_status, inquiry_id))
    conn.commit()
    conn.close()


def get_customer_email_by_inquiry(id_: int) -> str:
    """按询盘 id 查客户档案里归并过的邮箱。

    客户档案的邮箱来自该客户历史询盘（Email / Alibaba / WhatsApp / LinkedIn /
    展会 / CRM 等渠道）。当前询盘没提邮箱但档案里有 → 说明联系方式已知，
    不应再向客户追问。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT c.email FROM inquiries i
                 JOIN customers c ON i.customer_id = c.id
                 WHERE i.id=?""", (id_,))
    row = c.fetchone()
    conn.close()
    return (row[0] or "").strip() if row and row[0] else ""


def delete_inquiry(id_: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM inquiries WHERE id=?", (id_,))
    conn.commit()
    conn.close()


# ---------------- 客户档案 ----------------
def list_customers(filter_grade=None):
    """列出客户（按最近互动时间倒序）；filter_grade 可筛 A/B/C/D。

    返回 12 列：末位为 contacts JSON（第十轮，旧消费方按前 11 列解包不受影响）。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if filter_grade:
        c.execute(
            """SELECT id, company, country, email, contact_name,
                      inquiry_count, last_grade, last_score, last_seen, grade, note,
                      COALESCE(contacts, '[]')
               FROM customers WHERE grade=? ORDER BY last_seen DESC""",
            (filter_grade,),
        )
    else:
        c.execute(
            """SELECT id, company, country, email, contact_name,
                      inquiry_count, last_grade, last_score, last_seen, grade, note,
                      COALESCE(contacts, '[]')
               FROM customers ORDER BY last_seen DESC"""
        )
    rows = c.fetchall()
    conn.close()
    return rows


def update_customer_grade(cust_id: int, grade: str):
    """手动为客户定级（A/B/C/D），用于分类归档。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE customers SET grade=? WHERE id=?", (grade, cust_id))
    conn.commit()
    conn.close()


def update_customer_note(cust_id: int, note: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE customers SET note=? WHERE id=?", (note, cust_id))
    conn.commit()
    conn.close()


def customer_inquiries(cust_id: int):
    """取某客户名下的所有询盘（用于客户详情页）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, created_at, grade, score, country, company, contact_name "
        "FROM inquiries WHERE customer_id=? ORDER BY id DESC",
        (cust_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def delete_customer(cust_id: int):
    """删除客户，同时把其询盘的 customer_id 置空（询盘记录保留）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE inquiries SET customer_id=NULL WHERE customer_id=?", (cust_id,))
    c.execute("DELETE FROM customers WHERE id=?", (cust_id,))
    conn.commit()
    conn.close()


def update_inquiry_note(id_: int, note: str):
    """给某条询盘加/改备注（导出 Excel 时会带上）。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE inquiries SET note=? WHERE id=?", (note, id_))
    conn.commit()
    conn.close()


def get_inquiry_note(id_: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT note FROM inquiries WHERE id=?", (id_,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ""


def update_inquiry_info(id_: int, patch: dict):
    """手动补全 / 修正某条询盘的客户关键信息。

    patch 可包含：country / company / website / email / contact_name
    空字符串会被当成"清空"。改动会同时写回：
      1) report_json 里的 extracted（界面下次读取即生效）
      2) inquiries 表的冗余列（侧边栏/导出用）
      3) 关联的客户档案（保持客户档案与询盘一致）
    """
    keys = ("country", "company", "website", "email", "contact_name")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT report_json, customer_id FROM inquiries WHERE id=?", (id_,))
    row = c.fetchone()
    if not row:
        conn.close()
        return
    rep = json.loads(row[0])
    info = rep.setdefault("extracted", {})
    for k in keys:
        if k in patch:
            info[k] = (patch[k] or "").strip() or None

    c.execute(
        "UPDATE inquiries SET report_json=?, country=?, company=?, contact_name=? WHERE id=?",
        (json.dumps(rep, ensure_ascii=False),
         info.get("country"), info.get("company"), info.get("contact_name"),
         id_),
    )
    cid = row[1]
    if cid:
        # 客户档案同步（COALESCE：只在新值非空时覆盖，避免误清空）
        c.execute(
            """UPDATE customers SET
                   country=COALESCE(?, country),
                   company=COALESCE(?, company),
                   email=COALESCE(?, email),
                   website=COALESCE(?, website),
                   contact_name=COALESCE(?, contact_name)
               WHERE id=?""",
            (info.get("country"), info.get("company"), info.get("email"),
             info.get("website"), info.get("contact_name"), cid),
        )
    conn.commit()
    conn.close()


def dump_inquiries():
    """导出用：返回全部询盘的扁平化字典列表（含等级/产品/数量/意向/备注）。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """SELECT i.id, i.created_at, i.grade, i.score, i.country, i.company,
                  i.contact_name, i.note, c.grade AS cust_grade, i.report_json,
                  COALESCE(i.status, '待处理') AS status
           FROM inquiries i LEFT JOIN customers c ON i.customer_id = c.id
           ORDER BY i.id DESC"""
    )
    rows = c.fetchall()
    conn.close()
    out = []
    for (id_, created, grade, score, country, company, contact, note,
         cust_grade, rep_json, status) in rows:
        rep = json.loads(rep_json)
        info = rep.get("extracted", {})
        matches = rep.get("matches", [])
        first = matches[0] if matches else {}
        out.append({
            "id": id_,
            "created_at": created,
            "线索等级": grade or "",
            "客户等级": cust_grade or grade or "",
            "score": score,
            "处理状态": status,
            "country": country or "",
            "company": company or "",
            "contact_name": contact or "",
            "email": info.get("email") or "",
            "website": info.get("website") or "",
            # 中文别名（导出 Excel 时当表头用）
            "国家": country or "",
            "公司": company or "",
            "联系人": contact or "",
            "邮箱": info.get("email") or "",
            "网址": info.get("website") or "",
            "分数": score or "",
            "产品名称": first.get("name_cn") or first.get("name") or "",
            "咨询数量": (f"{info.get('quantity')} {info.get('quantity_unit')}"
                         if info.get("quantity") else ""),
            "采购意向": info.get("intent") or "",
            "紧急度": info.get("urgency") or "",
            "备注": note or "",
            "draft": rep.get("draft", "") or "",
        })
    return out
