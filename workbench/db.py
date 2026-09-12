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
# CRM v1：独立商机、报价版本、阶段历史和任务均可持久化，不再由询盘临时派生。
R15_CRM_OBJECTS = True
# Round 2：Pipeline 活动、健康度同步、赢单/输单确认。
R16_DEAL_PIPELINE = True
# Round 3（第十八轮）：crm_tasks 升级为独立 Follow-up Task（reason/fu_status/
# next_action/note 列）+ create/update/list/sent/resolve 函数。
R18_FOLLOWUP = True

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
    # CRM 核心对象。一个客户可有多个独立商机；询盘仅是商机的来源之一。
    c.execute("""CREATE TABLE IF NOT EXISTS opportunities (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER, inquiry_id INTEGER,
        title TEXT NOT NULL, product TEXT, stage TEXT NOT NULL DEFAULT 'NEW',
        amount REAL, currency TEXT DEFAULT 'USD', expected_close TEXT,
        probability INTEGER DEFAULT 10, owner TEXT DEFAULT '销售', next_action TEXT,
        next_action_at TEXT, lost_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    for col, ddl in (
        ("source", "TEXT"), ("notes", "TEXT"), ("details_json", "TEXT DEFAULT '{}'") ,
        ("last_activity_at", "TEXT"), ("manual_override", "INTEGER DEFAULT 0"),
        ("po_number", "TEXT"), ("final_value", "REAL"),
    ):
        try:
            c.execute(f"ALTER TABLE opportunities ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass
    c.execute("""CREATE TABLE IF NOT EXISTS opportunity_stage_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER NOT NULL,
        from_stage TEXT, to_stage TEXT NOT NULL, changed_at TEXT NOT NULL,
        actor TEXT DEFAULT '销售', reason TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER NOT NULL,
        version INTEGER NOT NULL, amount REAL, currency TEXT DEFAULT 'USD',
        valid_until TEXT, status TEXT DEFAULT 'DRAFT', created_at TEXT NOT NULL,
        UNIQUE(opportunity_id, version)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS crm_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER NOT NULL,
        title TEXT NOT NULL, due_at TEXT, owner TEXT DEFAULT '销售',
        status TEXT DEFAULT 'OPEN', created_at TEXT NOT NULL, completed_at TEXT
    )""")
    # 第十八轮（ROUND 3）：crm_tasks 升级为独立 Follow-up Task（跟进台专用）。
    # reason      = FollowUpReason 类型码（REPLY_DUE / QUOTE_SENT_NO_REPLY / …）
    # fu_status   = 独立 FollowUpStatus（PENDING/WAITING_CUSTOMER/SNOOZED/COMPLETED/CANCELLED）
    #               —— 与 InquiryStatus / DealStage 是三个不同维度；DUE_TODAY/OVERDUE 由
    #               due_at 即时计算（见 followup.fu_status_of），不落库避免陈旧。
    # next_action = 该跟进项的唯一下一步（单 NBA）
    # note        = 业务员备注
    for _fu_col, _fu_ddl in (("reason", "TEXT DEFAULT ''"),
                             ("fu_status", "TEXT DEFAULT 'PENDING'"),
                             ("next_action", "TEXT DEFAULT ''"),
                             ("note", "TEXT DEFAULT ''")):
        try:
            c.execute(f"ALTER TABLE crm_tasks ADD COLUMN {_fu_col} {_fu_ddl}")
        except sqlite3.OperationalError:
            pass
    c.execute("""CREATE TABLE IF NOT EXISTS deal_activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER NOT NULL,
        inquiry_id INTEGER, type TEXT NOT NULL, ts TEXT NOT NULL,
        description TEXT, actor TEXT DEFAULT '销售', metadata_json TEXT DEFAULT '{}'
    )""")
    # 旧版阶段名迁移为统一 Pipeline 配置；保留阶段历史。
    c.execute("UPDATE opportunities SET stage='REQUIREMENT_CONFIRMED', probability=35 WHERE stage='SAMPLE_TECH'")
    c.execute("UPDATE opportunities SET stage='NEGOTIATION', probability=70 WHERE stage='NEGOTIATING'")
    c.execute("UPDATE opportunities SET stage='PO_PENDING', probability=90 WHERE stage='PO'")
    c.execute("UPDATE opportunities SET stage='NEW', probability=10 WHERE stage='NURTURE'")
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
    """根据询盘里的客户信息归并到客户档案，返回 customer_id。

    TEST03：同一公司不同联系人/不同邮箱的多次询盘必须归并到同一客户。
    查找顺序：① 邮箱精确匹配 → ② 公司名精确匹配（老客户档案可能只有公司名，
    没有邮箱——如 BrightPromo BV 的既有档案 ckey=company:brightpromo bv）。
    全部不命中才新建，绝不产生"同一公司两个客户档案"。
    """
    keys = []
    email = (info.get("email") or "").strip().lower()
    company = (info.get("company") or "").strip().lower()
    if email and "@" in email:
        keys.append("email:" + email)
    if company:
        keys.append("company:" + company)
    if not keys:
        return None
    now = _now()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    row = None
    for key in keys:
        c.execute("SELECT id, inquiry_count, contacts FROM customers WHERE ckey=?",
                  (key,))
        row = c.fetchone()
        if row:
            break
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
        # 优先以邮箱建档案；没有邮箱才用公司名
        key = keys[0]
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
    # 新消息先按既有 Deal identity 归属：同一商业机会追加往来和事实；
    # 只有确实没有既有 Deal 时才初始化一个 NEW 商机。
    create_opportunity_from_inquiry(cust_id, new_id, report)
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



def _customer_requested_outcome_for_need(rep: dict):
    try:
        import queue_ui as _ui
        return _ui.customer_requested_outcome_from_text(
            (rep.get("extracted") or {}).get("raw_text") or rep.get("raw_text") or "")
    except Exception:
        return ""

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
        _raw_product = str(_m[0].get("name_cn") or _m[0].get("name") or "").strip()
        _s0 = _m[0].get("match_score")
        _weak = str(_m[0].get("match_source") or "").lower() == "closest_heuristic"
        product = "" if _weak else _raw_product
        match_top = {"name": product or _raw_product,
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
        "product": str(info.get("product_query") or "").strip() or product,
        "product_cat": product_cat,
        "match_top": match_top,
        "lead_summary": _lead_summary(rep),
        "has_draft": bool((rep.get("draft") or "").strip()),
        "qty_num": qty if isinstance(qty, (int, float)) else None,
        "price_num": price_num,
        "target_price_currency": info.get("target_price_currency") or "",
        "blockers": [str(b.get("field", "")) for b in (qr.get("blockers_preliminary") or [])],
        "readiness": qr.get("quotation_readiness_status") or "",
        "raw_text": rep.get("raw_text") or info.get("raw_text") or "",
        "customer_requested_outcome": _customer_requested_outcome_for_need(rep),
        # 第十七轮：客户产品短语（只读派生，供首页"一个 Deal 一行"展示聚合用；
        #  不参与评分/匹配/回复，也不回写库）
        "product_query": str(info.get("product_query") or "")[:120],
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


# ---------------- 独立 CRM 商机 / 报价 / 任务 ----------------
def _opportunity_dict(row):
    keys = ("id", "customer_id", "inquiry_id", "title", "product", "stage", "amount",
            "currency", "expected_close", "probability", "owner", "next_action",
            "next_action_at", "lost_reason", "created_at", "updated_at", "company", "country",
            "contact", "email", "source", "notes", "details_json", "last_activity_at",
            "manual_override", "po_number", "final_value")
    result = dict(zip(keys, row))
    try:
        details = json.loads(result.pop("details_json") or "{}")
    except (ValueError, TypeError):
        details = {}
    result.update({k: v for k, v in details.items() if k not in result or not result[k]})
    result["details"] = details
    return result


_DEAL_CLOSED_STAGE = {"WON", "LOST", "NURTURE", "ON_HOLD"}
_DEAL_SIG_STOP = {
    "with", "and", "or", "the", "a", "an", "of", "for", "in", "on", "to",
    "custom", "customized", "logo", "packaging", "package", "please", "new",
    "pcs", "pc", "units", "unit", "sets", "set", "pieces", "piece",
    "black", "white", "color", "colour", "size", "oem", "odm",
}


def _deal_product_signature(text, n: int = 3) -> str:
    """DB-level commercial opportunity signature.

    This mirrors the queue-level signature so Deal creation and Deal display use
    the same identity rule: same customer/company + same product subject +
    active stage = one Deal.
    """
    import re as _re
    toks = _re.findall(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-]*",
                       str(text or "").lower())
    keep = []
    for t in toks:
        if not t or t in _DEAL_SIG_STOP:
            continue
        if _re.fullmatch(r"[\d\.\-]+", t):
            continue
        if t.endswith(("h", "ml", "mm", "cm", "kg", "g")) and _re.match(r"^[\d\.]+", t):
            continue
        keep.append(t)
        if len(keep) >= n:
            break
    return " ".join(keep)


def _company_key(value) -> str:
    return " ".join(str(value or "").split()).lower()


def _append_unique(seq, value):
    out = list(seq or [])
    if value in (None, ""):
        return out
    if value not in out:
        out.append(value)
    return out


def _inquiry_report_bits(conn, inquiry_id):
    if not inquiry_id:
        return {}
    try:
        c = conn.cursor()
        c.execute("SELECT report_json,source_text,company FROM inquiries WHERE id=?", (inquiry_id,))
        row = c.fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    raw_report, source_text, company = row
    try:
        report = json.loads(raw_report or "{}")
    except Exception:
        report = {}
    info = report.get("extracted") or {}
    text = str(info.get("raw_text") or source_text or "")
    product = str(info.get("product_query") or info.get("product") or "").strip()
    import re as _re
    company_key = _company_key(company or info.get("company"))
    product_key = _company_key(product)
    if (not product) or (company_key and product_key == company_key) or "gmbh" in product_key:
        m = _re.search(r"product\s*:\s*([^\n\r]+)", text, _re.I)
        if m:
            product = m.group(1).strip(" -*;,.：")
        elif _re.search(r"wireless\s+anc\s+earbuds?", text, _re.I):
            product = "Wireless ANC Earbuds"
        elif _re.search(r"insulated\s+food\s+container", text, _re.I):
            product = "Insulated Food Container"
    qty = info.get("quantity") or info.get("qty")
    if not qty:
        import re as _re
        m = _re.search(r"(?:quantity\s*:\s*)?([\d,]+)\s*pcs", text, _re.I)
        if m:
            qty = m.group(1).replace(",", "")
    return {"product": product, "quantity": qty, "company": company or info.get("company")}




def _canonical_product_name(text: str) -> str:
    import re as _re
    t = str(text or "")
    if _re.search(r"wireless\s+anc\s+earbuds?", t, _re.I):
        return "Wireless ANC Earbuds"
    if _re.search(r"insulated\s+food\s+container", t, _re.I):
        return "Insulated Food Container"
    return t.strip()

def _valid_product_identity(product: str, company: str = "") -> bool:
    p = str(product or "").strip()
    if not p:
        return False
    pk = _company_key(p)
    ck = _company_key(company)
    if ck and pk == ck:
        return False
    if "gmbh" in pk or "ltd" in pk or "electronics" in pk and "earbud" not in pk:
        return False
    return bool(_deal_product_signature(p))


def _opportunity_identity_product(conn, row, details: dict) -> str:
    bits = _inquiry_report_bits(conn, row["inquiry_id"])
    company = row["customer_company"] if "customer_company" in row.keys() else bits.get("company")
    for candidate in (details.get("product_query"), row["product"], bits.get("product"), row["title"]):
        clean = _canonical_product_name(candidate)
        if _valid_product_identity(clean, company):
            return clean
    return _canonical_product_name(bits.get("product") or "")

def _find_existing_opportunity(conn, customer_id, company: str, product: str):
    """Find an active existing Deal for the same commercial opportunity."""
    sig = _deal_product_signature(product)
    if not sig:
        return None
    company_key = _company_key(company)
    c = conn.cursor()
    c.execute("""SELECT o.id,o.customer_id,o.inquiry_id,o.title,o.product,o.stage,
                        o.details_json,c.company
                 FROM opportunities o
                 LEFT JOIN customers c ON c.id=o.customer_id
                 WHERE COALESCE(o.stage,'NEW') NOT IN ('WON','LOST','NURTURE','ON_HOLD')
                 ORDER BY o.updated_at DESC, o.id DESC""")
    for row in c.fetchall():
        oid, cid, inquiry_id, title, opp_product, stage, raw_details, cust_company = row
        same_customer = bool(customer_id and cid == customer_id)
        same_company = bool(company_key and company_key == _company_key(cust_company))
        if not (same_customer or same_company):
            continue
        try:
            details = json.loads(raw_details or "{}")
        except Exception:
            details = {}
        fake_row = {"inquiry_id": inquiry_id, "product": opp_product, "title": title, "customer_company": cust_company}
        old_product = _opportunity_identity_product(conn, fake_row, details)
        if _deal_product_signature(old_product) == sig:
            return {"id": oid, "customer_id": cid, "inquiry_id": inquiry_id,
                    "product": opp_product, "stage": stage, "details": details}
    return None


def _merge_opportunity_details(existing: dict, inquiry_id, report: dict,
                               product: str, details: dict) -> dict:
    merged = dict((existing or {}).get("details") or {})
    related = _append_unique(merged.get("related_inquiry_ids"), existing.get("inquiry_id"))
    related = _append_unique(related, inquiry_id)
    merged["related_inquiry_ids"] = related
    merged["current_inquiry_id"] = inquiry_id
    merged["product_query"] = details.get("product_query") or merged.get("product_query") or product
    for key, value in details.items():
        if value not in (None, "", []):
            merged[key] = value
    qty = details.get("quantity")
    if qty not in (None, ""):
        history = _append_unique(merged.get("quantity_history"), qty)
        merged["quantity_history"] = history
        merged["quantity"] = qty
    return merged


def create_opportunity_from_inquiry(customer_id, inquiry_id, report: dict) -> int:
    """将一条询盘归属到既有 Deal，或创建一个新的 Deal。

    TEST03：客户产品短语（product_query）在库内无匹配时仍是"客户要的产品"——
    商机 product 列与标题回退到客户原话，保证「同一客户 + 新产品 = 新 Deal」；
    库内匹配与客户需求两件事分别存档（details_json 里同时保存库内匹配与商机分类）。
    """
    info = report.get("extracted") or {}
    matches = report.get("matches") or []
    insight = report.get("insight") or {}
    first = matches[0] if matches and isinstance(matches[0], dict) else {}
    _query = str(info.get("product_query") or "").strip()
    _pm_probe = (insight.get("product_match") or {})
    _match_status = str(_pm_probe.get("product_match_status") or "").upper()
    _weak_match = (str(first.get("match_source") or "").lower() == "closest_heuristic"
                   or _match_status in {"NO_MATCH", "UNRESOLVED", "INSUFFICIENT_INFORMATION"})
    # 客户产品名是 Deal 线程的业务主语；内部低置信候选只保存在匹配信息里，
    # 不能覆盖客户明确提出的产品，否则同一客户复询会被拆成错误商机。
    product = _query[:90] if (_query and _weak_match) else str(first.get("name_cn") or first.get("name") or "").strip()
    if not product and _query:
        product = _query[:90]
    details = {
        "quantity": info.get("quantity") or info.get("qty"),
        "specification": info.get("specification") or info.get("specs"),
        "customization": info.get("customization") or info.get("custom_requirement"),
        "certification": info.get("certification"), "moq": info.get("moq"),
        "target_price": info.get("target_price"), "incoterm": info.get("incoterm"),
        "destination": info.get("destination"), "payment_term": info.get("payment_term"),
        "material": info.get("material"), "capacity": info.get("capacity"),
        "colors": info.get("colors"), "lead_time": info.get("lead_time"),
        "samples": info.get("samples"),
        "ai_score": report.get("score"), "intent": report.get("intent"),
    }
    # TEST03：产品分类 / 客户需求状态 / 竞品报价 随商机存档（只读派生，不改评分）
    _pm = (insight.get("product_match") or {})
    details.update({
        "product_query": _query,
        "product_match_status": _pm.get("product_match_status"),
        "opportunity_type": _pm.get("opportunity_type"),
        "supplier_capability_status": _pm.get("supplier_capability_status"),
        "requirement_completeness": (insight.get("requirement_completeness") or {}).get("level"),
        "competitor_price": info.get("competitor_price"),
        "competitor_price_note": info.get("competitor_price_note"),
    })
    title = " · ".join(x for x in (str(info.get("company") or "").strip(), product) if x)
    title = title or f"询盘 #{inquiry_id}"
    now = _now()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM opportunities WHERE inquiry_id=?", (inquiry_id,))
    row = c.fetchone()
    if row:
        conn.close()
        return row[0]

    existing = _find_existing_opportunity(
        conn, customer_id, info.get("company"), product)
    if existing:
        merged = _merge_opportunity_details(existing, inquiry_id, report,
                                            product, details)
        # DealStage 是已持久化的销售流程状态。新的询盘、客户回复或需求修订
        # 只会更新消息关联和当前事实，绝不能把既有 Deal 重置为 NEW。
        c.execute("""UPDATE opportunities
                     SET details_json=?, product=COALESCE(NULLIF(product,''), ?),
                         title=COALESCE(NULLIF(title,''), ?),
                         last_activity_at=?, updated_at=?
                     WHERE id=?""",
                  (json.dumps(merged, ensure_ascii=False), product or None,
                   title, now, now, existing["id"]))
        conn.commit()
        conn.close()
        return existing["id"]

    details["related_inquiry_ids"] = [inquiry_id]
    details["current_inquiry_id"] = inquiry_id
    if details.get("quantity") not in (None, ""):
        details["quantity_history"] = [details.get("quantity")]
    c.execute("""INSERT INTO opportunities
        (customer_id, inquiry_id, title, product, source, details_json, last_activity_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)""", (customer_id, inquiry_id, title, product or None,
        info.get("source") or report.get("source") or "询盘", json.dumps(details, ensure_ascii=False), now, now, now))
    oid = c.lastrowid
    c.execute("""INSERT INTO opportunity_stage_history
        (opportunity_id, from_stage, to_stage, changed_at, actor, reason)
        VALUES (?,?,?,?,?,?)""", (oid, None, "NEW", now, "系统", "由询盘创建"))
    conn.commit()
    conn.close()
    return oid


def backfill_opportunities() -> int:
    """为旧版已有询盘一次性补建独立商机，已关联的询盘不会重复创建。

    ROUND 7.2 修复（性能 + 正确性）：
      「已关联」不能只看 `opportunities.inquiry_id`。一个 Deal 会吸收同一客户
      的多次往来，主询盘只写 `inquiry_id`，其余往来记在
      `details_json.related_inquiry_ids` / `current_inquiry_id` 里。
      旧查询只 JOIN 主键列，导致这 10 条询盘被永远判为「未关联」，
      每次 rerun 都重跑一遍 → 侧栏切换卡顿，且反复重写 details_json/updated_at。

      现在改为：先排除主键列已关联的，再排除 related_inquiry_ids 里的，
      真正需要补建的才处理。全部已关联时查询结果为空，零成本返回。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT i.id, i.customer_id, i.report_json FROM inquiries i
                 LEFT JOIN opportunities o ON o.inquiry_id=i.id
                 WHERE o.id IS NULL""")
    candidates = c.fetchall()
    if not candidates:
        conn.close()
        return 0
    # 收集已被任何 Deal 通过 related_inquiry_ids / current_inquiry_id 关联的询盘 id
    c.execute("SELECT inquiry_id, details_json FROM opportunities")
    linked = set()
    for main_iid, raw in c.fetchall():
        if main_iid:
            linked.add(main_iid)
        try:
            details = json.loads(raw or "{}")
        except Exception:
            details = {}
        for iid in (details.get("related_inquiry_ids") or []):
            if iid:
                linked.add(iid)
        if details.get("current_inquiry_id"):
            linked.add(details["current_inquiry_id"])
    conn.close()
    rows = [r for r in candidates if r[0] not in linked]
    created = 0
    for inquiry_id, customer_id, raw in rows:
        try:
            report = json.loads(raw or "{}")
        except Exception:
            report = {}
        create_opportunity_from_inquiry(customer_id, inquiry_id, report)
        created += 1
    return created


def reconcile_duplicate_opportunities() -> int:
    """Merge duplicate active Deals created from follow-up messages.

    Identity is the same source rule used at creation time: same customer/company
    plus same commercial product signature, excluding closed stages.  The newest
    Deal becomes the keeper so current quantity reflects the latest revision;
    older inquiry ids and quantities are retained in details_json history.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""SELECT o.*, c.company AS customer_company
                 FROM opportunities o
                 LEFT JOIN customers c ON c.id=o.customer_id
                 WHERE COALESCE(o.stage,'NEW') NOT IN ('WON','LOST','NURTURE','ON_HOLD')
                 ORDER BY o.updated_at ASC, o.id ASC""")
    rows = c.fetchall()
    groups = {}
    for r in rows:
        try:
            details = json.loads(r["details_json"] or "{}")
        except Exception:
            details = {}
        company = _company_key(r["customer_company"] or "")
        product = _opportunity_identity_product(conn, r, details)
        sig = _deal_product_signature(product)
        if not company or not sig:
            continue
        groups.setdefault((company, sig), []).append((r, details))

    merged_count = 0
    now = _now()
    for _, members in groups.items():
        if len(members) < 2:
            continue
        # 最新记录提供当前事实（例如最近修订后的数量），但阶段是持久化的
        # 流程状态。历史库中的重复行可能带着一个较新的 NEW，不能因此把
        # 已报价/样品/谈判中的 Deal 回退到 NEW。
        members_sorted = sorted(members, key=lambda x: (str(x[0]["updated_at"] or ""), int(x[0]["id"])))
        keeper, keeper_details = members_sorted[-1]
        keeper_id = keeper["id"]
        from sales_crm import STAGES, PROBABILITY
        stage_rank = {stage: index for index, stage in enumerate(STAGES)}
        preserved_stage = max(
            (str(row["stage"] or "NEW") for row, _ in members_sorted),
            key=lambda stage: stage_rank.get(stage, -1),
        )
        merged_details = dict(keeper_details or {})
        related = list(merged_details.get("related_inquiry_ids") or [])
        qty_history = list(merged_details.get("quantity_history") or [])
        for row, details in members_sorted:
            for iid in [row["inquiry_id"], details.get("current_inquiry_id")]:
                related = _append_unique(related, iid)
            for iid in (details.get("related_inquiry_ids") or []):
                related = _append_unique(related, iid)
            qty = details.get("quantity") or _inquiry_report_bits(conn, row["inquiry_id"]).get("quantity")
            if qty not in (None, ""):
                qty_history = _append_unique(qty_history, qty)
            for key, value in (details or {}).items():
                if key in {"quantity", "related_inquiry_ids", "current_inquiry_id", "quantity_history"}:
                    continue
                if value not in (None, "", []) and key not in merged_details:
                    merged_details[key] = value
        latest_qty = (keeper_details or {}).get("quantity") or _inquiry_report_bits(conn, keeper["inquiry_id"]).get("quantity")
        if latest_qty not in (None, ""):
            qty_history = _append_unique(qty_history, latest_qty)
            merged_details["quantity"] = latest_qty
        merged_details["quantity_history"] = [x for x in qty_history if x not in (None, "")]
        merged_details["related_inquiry_ids"] = [x for x in related if x not in (None, "")]
        merged_details["current_inquiry_id"] = keeper["inquiry_id"]
        clean_product = _opportunity_identity_product(conn, keeper, merged_details)
        if clean_product:
            merged_details["product_query"] = clean_product

        duplicate_ids = [row["id"] for row, _ in members_sorted if row["id"] != keeper_id]
        for dup_id in duplicate_ids:
            for table in ("quotes", "crm_tasks", "followup_tasks", "opportunity_stage_history", "deal_activity"):
                try:
                    c.execute(f"UPDATE {table} SET opportunity_id=? WHERE opportunity_id=?", (keeper_id, dup_id))
                except sqlite3.OperationalError:
                    pass
            c.execute("DELETE FROM opportunities WHERE id=?", (dup_id,))
            merged_count += 1
        c.execute("SELECT company FROM customers WHERE id=?", (keeper["customer_id"],))
        _co_row = c.fetchone()
        _co_name = (_co_row[0] if _co_row else "") or ""
        _clean_title = " · ".join(x for x in (_co_name, clean_product) if x) or keeper["title"]
        c.execute("""UPDATE opportunities
                     SET details_json=?, product=?, title=?, stage=?, probability=?, updated_at=?, last_activity_at=?
                     WHERE id=?""",
                   (json.dumps(merged_details, ensure_ascii=False), clean_product or keeper["product"],
                    _clean_title, preserved_stage, PROBABILITY.get(preserved_stage, keeper["probability"]),
                    now, now, keeper_id))
    # Also clean legacy product/title fields for already-merged single Deals.
    c.execute("""SELECT o.*, c.company AS customer_company
                 FROM opportunities o
                 LEFT JOIN customers c ON c.id=o.customer_id
                 WHERE COALESCE(o.stage,'NEW') NOT IN ('WON','LOST','NURTURE','ON_HOLD')""")
    for row in c.fetchall():
        try:
            details = json.loads(row["details_json"] or "{}")
        except Exception:
            details = {}
        clean_product = _opportunity_identity_product(conn, row, details)
        if not clean_product:
            continue
        dirty_product = str(row["product"] or "")
        dirty_title = str(row["title"] or "")
        if dirty_product != clean_product or clean_product not in dirty_title:
            details["product_query"] = clean_product
            clean_title = " · ".join(x for x in (row["customer_company"], clean_product) if x) or dirty_title
            c.execute("""UPDATE opportunities
                         SET product=?, title=?, details_json=?, updated_at=?
                         WHERE id=?""",
                      (clean_product, clean_title,
                       json.dumps(details, ensure_ascii=False), now, row["id"]))
    conn.commit()
    conn.close()
    return merged_count

def list_opportunities(stage=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    sql = """SELECT o.id,o.customer_id,o.inquiry_id,o.title,o.product,o.stage,o.amount,
                     o.currency,o.expected_close,o.probability,o.owner,o.next_action,
                     o.next_action_at,o.lost_reason,o.created_at,o.updated_at,
                     c.company,c.country,c.contact_name,c.email,o.source,o.notes,o.details_json,
                     o.last_activity_at,o.manual_override,o.po_number,o.final_value
              FROM opportunities o LEFT JOIN customers c ON c.id=o.customer_id"""
    if stage:
        c.execute(sql + " WHERE o.stage=? ORDER BY o.updated_at DESC, o.id DESC", (stage,))
    else:
        c.execute(sql + " ORDER BY o.updated_at DESC, o.id DESC")
    rows = [_opportunity_dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_opportunity(opportunity_id):
    rows = [o for o in list_opportunities() if o["id"] == opportunity_id]
    return rows[0] if rows else None


def update_opportunity(opportunity_id, patch: dict):
    allowed = {"title", "product", "amount", "currency", "expected_close", "owner",
               "next_action", "next_action_at", "lost_reason", "source", "notes",
               "po_number", "final_value", "manual_override"}
    values = {k: patch[k] for k in allowed if k in patch}
    if "details" in patch:
        existing = get_opportunity(opportunity_id) or {}
        details = dict(existing.get("details") or {})
        details.update(patch["details"] or {})
        values["details_json"] = json.dumps(details, ensure_ascii=False)
    if not values:
        return False
    values["updated_at"] = _now()
    fields = ", ".join(f"{k}=?" for k in values)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"UPDATE opportunities SET {fields} WHERE id=?", (*values.values(), opportunity_id))
    conn.commit()
    changed = c.rowcount > 0
    conn.close()
    return changed


def move_opportunity_stage(opportunity_id, target, actor="销售", reason="", manual_override=False):
    from sales_crm import PROBABILITY
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT stage FROM opportunities WHERE id=?", (opportunity_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    source, now = row[0], _now()
    if source == target:
        conn.close()
        return True
    c.execute("UPDATE opportunities SET stage=?, probability=?, manual_override=?, last_activity_at=?, updated_at=? WHERE id=?",
              (target, PROBABILITY.get(target, 0), int(bool(manual_override)), now, now, opportunity_id))
    c.execute("""INSERT INTO opportunity_stage_history
        (opportunity_id,from_stage,to_stage,changed_at,actor,reason) VALUES (?,?,?,?,?,?)""",
              (opportunity_id, source, target, now, actor, reason or None))
    c.execute("SELECT inquiry_id FROM opportunities WHERE id=?", (opportunity_id,))
    inquiry_id = c.fetchone()[0]
    c.execute("""INSERT INTO deal_activity
        (opportunity_id,inquiry_id,type,ts,description,actor,metadata_json) VALUES (?,?,?,?,?,?,?)""",
        (opportunity_id, inquiry_id, "STAGE_CHANGE", now, f"{source} → {target}", actor,
         json.dumps({"from": source, "to": target, "reason": reason, "manual_override": bool(manual_override)}, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return True


def list_stage_history(opportunity_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT from_stage,to_stage,changed_at,actor,COALESCE(reason,'') FROM opportunity_stage_history WHERE opportunity_id=? ORDER BY id DESC", (opportunity_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def list_quotes(opportunity_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id,version,amount,currency,valid_until,status,created_at FROM quotes WHERE opportunity_id=? ORDER BY version DESC", (opportunity_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def create_quote(opportunity_id, amount, currency, valid_until, status="DRAFT"):
    status = str(status or "DRAFT").upper()
    if status not in ("DRAFT", "SENT", "REVISED", "ACCEPTED", "REJECTED", "EXPIRED"):
        status = "DRAFT"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(version),0)+1 FROM quotes WHERE opportunity_id=?", (opportunity_id,))
    version = c.fetchone()[0]
    c.execute("INSERT INTO quotes (opportunity_id,version,amount,currency,valid_until,status,created_at) VALUES (?,?,?,?,?,?,?)",
              (opportunity_id, version, amount, currency, valid_until or None, status, _now()))
    c.execute("UPDATE opportunities SET amount=?,currency=?,updated_at=? WHERE id=?",
              (amount, currency, _now(), opportunity_id))
    conn.commit()
    conn.close()
    record_deal_activity(opportunity_id, "QUOTATION_SENT" if status in ("SENT", "REVISED") else "QUOTATION_CREATED",
                         f"报价 v{version}: {currency} {amount:,.2f}")
    if status in ("SENT", "REVISED"):
        try:
            move_opportunity_stage(opportunity_id, "QUOTED", actor="销售",
                                   reason=f"报价 v{version} 已发送")
            ensure_quote_followup(opportunity_id, version, actor="销售")
        except Exception:
            pass
    return version


def update_quote_status(quote_id, status, actor="销售"):
    """更新报价生命周期状态，并在显式 SENT 时推进到 QUOTED。"""
    status = str(status or "").upper()
    if status not in ("DRAFT", "SENT", "REVISED", "ACCEPTED", "REJECTED", "EXPIRED"):
        return False
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT opportunity_id,version,amount,currency,status
                 FROM quotes WHERE id=?""", (quote_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    oid, version, amount, currency, old_status = row
    if old_status == status:
        conn.close()
        return True
    c.execute("UPDATE quotes SET status=? WHERE id=?", (status, quote_id))
    conn.commit()
    conn.close()
    ev = {
        "SENT": "QUOTATION_SENT",
        "REVISED": "QUOTATION_REVISED",
        "ACCEPTED": "QUOTATION_ACCEPTED",
        "REJECTED": "QUOTATION_REJECTED",
        "EXPIRED": "QUOTATION_EXPIRED",
    }.get(status, "QUOTATION_UPDATED")
    record_deal_activity(oid, ev,
                         f"报价 v{version}: {old_status} → {status}",
                         actor=actor,
                         metadata={"quote_id": quote_id, "amount": amount,
                                   "currency": currency})
    if status in ("SENT", "REVISED"):
        move_opportunity_stage(oid, "QUOTED", actor=actor,
                               reason=f"报价 v{version} 已发送")
        ensure_quote_followup(oid, version, actor=actor)
    elif status == "ACCEPTED":
        move_opportunity_stage(oid, "PO_PENDING", actor=actor,
                               reason=f"报价 v{version} 已接受，等待 PO")
        update_opportunity(oid, {"next_action": "确认 PO 与付款条款"})
    elif status == "REJECTED":
        update_opportunity(oid, {"next_action": "处理报价异议或创建修订报价"})
    return True


def ensure_quote_followup(opportunity_id, version=None, actor="销售",
                          wait_days: int = 3):
    """报价发送后创建/复用跟进任务，形成 Quote → Follow-up 闭环。"""
    due = (datetime.datetime.now() + datetime.timedelta(days=wait_days)).strftime("%Y-%m-%d 10:00")
    title = f"跟进报价 v{version}" if version else "跟进已发送报价"
    next_action = "确认客户是否收到报价并推进反馈"
    try:
        return create_followup_task(
            opportunity_id, "QUOTE_SENT_NO_REPLY", due_at=due, title=title,
            next_action=next_action, note="报价发送后自动创建", actor=actor,
            reuse=True)
    except Exception:
        return None, False


def list_crm_tasks(opportunity_id=None, open_only=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    sql = "SELECT id,opportunity_id,title,due_at,owner,status,created_at,completed_at FROM crm_tasks"
    where, args = [], []
    if opportunity_id is not None:
        where.append("opportunity_id=?"); args.append(opportunity_id)
    if open_only:
        where.append("status='OPEN'")
    if where: sql += " WHERE " + " AND ".join(where)
    c.execute(sql + " ORDER BY due_at IS NULL, due_at, id DESC", args)
    rows = c.fetchall()
    conn.close()
    return rows


def create_crm_task(opportunity_id, title, due_at="", owner="销售"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO crm_tasks (opportunity_id,title,due_at,owner,created_at) VALUES (?,?,?,?,?)",
              (opportunity_id, title, due_at or None, owner, _now()))
    c.execute("UPDATE opportunities SET next_action=?,next_action_at=?,updated_at=? WHERE id=?",
              (title, due_at or None, _now(), opportunity_id))
    conn.commit(); conn.close()
    record_deal_activity(opportunity_id, "FOLLOW_UP_CREATED", title, actor=owner,
                         metadata={"due_at": due_at})


def complete_crm_task(task_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT opportunity_id,title,owner FROM crm_tasks WHERE id=?", (task_id,))
    row = c.fetchone()
    c.execute("UPDATE crm_tasks SET status='DONE',completed_at=? WHERE id=?", (_now(), task_id))
    conn.commit(); conn.close()
    if row:
        record_deal_activity(row[0], "FOLLOW_UP_COMPLETED", row[1], actor=row[2])


# =====================================================================
# 第十八轮（ROUND 3）· Follow-up Task（跟进台，独立 FollowUpStatus）
# =====================================================================
# 复用 crm_tasks 存储；reason<>'' 的行即"跟进任务"。
# 状态三轴分离：InquiryStatus（沟通）/ DealStage（商机）/ fu_status（跟进任务）。
# 时间轴统一写 deal_activity（deal 级）以保持 Timeline 可追溯。
# =====================================================================

# fu_status 存储基态；DUE_TODAY / OVERDUE 由 followup.fu_status_of(due_at) 即时计算
FU_ACTIVE = ("PENDING", "WAITING_CUSTOMER", "SNOOZED")   # 未完结基态（防重复/统计口径）
FU_DONE = ("COMPLETED", "CANCELLED")


def _followup_event_cn(ev: str) -> str:
    return {"FOLLOW_UP_CREATED": "创建跟进", "FOLLOW_UP_COMPLETED": "完成跟进",
            "FOLLOW_UP_SNOOZED": "稍后提醒", "FOLLOW_UP_RESCHEDULED": "改期跟进",
            "FOLLOW_UP_WAITING": "等待客户回复", "FOLLOW_UP_REOPENED": "重新打开跟进",
            "FOLLOW_UP_EMAIL_DRAFTED": "生成跟进邮件",
            "FOLLOW_UP_SENT": "跟进已发送", "CUSTOMER_REPLIED": "客户回复",
            "FOLLOW_UP_CANCELLED": "取消跟进"}.get(ev, ev)


def _fu_timeline(opportunity_id: int, type_: str, task_title: str, actor: str,
                 metadata: dict = None, inquiry_id: int = None):
    """写一条跟进时间轴（deal_activity 主 + activity 从，若 inquiry 已知）。"""
    record_deal_activity(opportunity_id, type_,
                         f"{_followup_event_cn(type_)}：{task_title}", actor=actor,
                         metadata=metadata or {})
    if inquiry_id:
        try:
            record_activity(inquiry_id, type_,
                            f"{_followup_event_cn(type_)}：{task_title}",
                            actor=actor, result="")
        except Exception:
            pass


def create_followup_task(opportunity_id, reason, due_at="", title=None,
                         next_action="", note="", actor="销售",
                         reuse: bool = True) -> tuple:
    """创建（或复用）一条跟进任务。返回 (task_id, reused: bool)。

    去重（spec 25）：同一 Deal + 同一 reason 已有 active 任务（PENDING /
    WAITING_CUSTOMER / SNOOZED）时 → 复用并更新，绝不重复创建；
    历史 COMPLETED / CANCELLED 任务不破坏、不参与去重匹配。
    """
    reason = str(reason or "").strip()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if reuse:
        c.execute("""SELECT id, due_at FROM crm_tasks
                     WHERE opportunity_id=? AND reason=? AND fu_status IN
                     ('PENDING','WAITING_CUSTOMER','SNOOZED') ORDER BY id LIMIT 1""",
                  (opportunity_id, reason))
        row = c.fetchone()
        if row:
            tid, old_due = row
            sets, args = [], []
            if due_at and not old_due:
                sets.append("due_at=?"); args.append(due_at)
            if next_action:
                sets.append("next_action=?"); args.append(next_action)
            if note:
                sets.append("note=?"); args.append(note)
            if sets:
                args.append(tid)
                c.execute("UPDATE crm_tasks SET " + ", ".join(sets) +
                          " WHERE id=?", args)
            conn.commit(); conn.close()
            return tid, True
    now = _now()
    title = (title or reason or "跟进").strip()
    c.execute("""INSERT INTO crm_tasks
        (opportunity_id,title,due_at,owner,status,created_at,reason,fu_status,
         next_action,note)
        VALUES (?,?,?,?,'OPEN',?,?,?,?,?)""",
        (opportunity_id, title, due_at or None, actor, now, reason,
         "PENDING", next_action or "", note or ""))
    tid = c.lastrowid
    c.execute("UPDATE opportunities SET next_action=?,next_action_at=?,updated_at=?"
              " WHERE id=?", (title, due_at or None, now, opportunity_id))
    conn.commit(); conn.close()
    c2 = sqlite3.connect(DB_PATH)
    row = c2.execute("SELECT inquiry_id FROM opportunities WHERE id=?",
                     (opportunity_id,)).fetchone()
    c2.close()
    _fu_timeline(opportunity_id, "FOLLOW_UP_CREATED", title, actor,
                 metadata={"reason": reason, "due_at": due_at},
                 inquiry_id=row[0] if row else None)
    return tid, False


def update_followup_task(task_id, fu_status=None, due_at=None, reason=None,
                         title=None, note=None, next_action=None,
                         actor="销售", timeline: bool = True):
    """更新跟进任务并写 Timeline（每类动作独立事件，只写一次）。

    动作 → 事件：完成→FOLLOW_UP_COMPLETED；取消→FOLLOW_UP_CANCELLED；
    SNOOZE→FOLLOW_UP_SNOOZED；WAITING→FOLLOW_UP_WAITING；
    PENDING(重开)→FOLLOW_UP_REOPENED；仅改 due→FOLLOW_UP_RESCHEDULED。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT ft.opportunity_id, ft.title, ft.fu_status, ft.due_at,
                        o.inquiry_id
                 FROM crm_tasks ft LEFT JOIN opportunities o ON o.id=ft.opportunity_id
                 WHERE ft.id=?""", (task_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    deal_id, cur_title, cur_status, cur_due, inq_id = row
    sets, args = [], []
    new_status = cur_status
    if fu_status is not None:
        new_status = str(fu_status)
        sets.append("fu_status=?")
        args.append(new_status)
        if new_status in FU_DONE:
            sets.append("status='DONE'")
            sets.append("completed_at=?")
            args.append(_now())
        elif cur_status in FU_DONE and new_status in FU_ACTIVE:
            sets.append("status='OPEN'")
            sets.append("completed_at=NULL")
    if reason is not None:
        sets.append("reason=?"); args.append(str(reason))
    if title is not None:
        sets.append("title=?"); args.append(str(title))
    if note is not None:
        sets.append("note=?"); args.append(str(note))
    if next_action is not None:
        sets.append("next_action=?"); args.append(str(next_action))
    due_changed = False
    if due_at is not None and str(due_at) != str(cur_due or ""):
        sets.append("due_at=?"); args.append(due_at)
        due_changed = True
    if sets:
        args.append(task_id)
        c.execute("UPDATE crm_tasks SET " + ", ".join(sets) + " WHERE id=?",
                  args)
    conn.commit(); conn.close()
    if not timeline:
        return True
    title_now = title if title is not None else cur_title
    meta = {"reason": reason} if reason else {}
    ev = None
    if new_status == "COMPLETED" and cur_status != "COMPLETED":
        ev = "FOLLOW_UP_COMPLETED"
    elif new_status == "CANCELLED" and cur_status != "CANCELLED":
        ev = "FOLLOW_UP_CANCELLED"
    elif new_status == "SNOOZED" and cur_status != "SNOOZED":
        ev = "FOLLOW_UP_SNOOZED"
    elif new_status == "WAITING_CUSTOMER" and cur_status != "WAITING_CUSTOMER":
        ev = "FOLLOW_UP_WAITING"
    elif (cur_status in FU_DONE and new_status in FU_ACTIVE
          and new_status == "PENDING"):
        ev = "FOLLOW_UP_REOPENED"
    if ev is None and due_changed:
        ev = "FOLLOW_UP_RESCHEDULED"
    if ev:
        _fu_timeline(deal_id, ev, title_now, actor, metadata=meta,
                     inquiry_id=inq_id)
    return True


def list_followup_tasks(opportunity_id=None, fu_status=None, reason=None,
                        active_only: bool = False):
    """列出跟进任务（reason<>'' 的行），联出客户 / 商机上下文。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    sql = """SELECT ft.id, ft.opportunity_id, ft.title, ft.due_at, ft.owner,
                    ft.status, ft.created_at, ft.completed_at, ft.reason,
                    ft.fu_status, ft.next_action, ft.note,
                    o.stage, o.product, o.customer_id, o.inquiry_id,
                    o.amount, o.currency, o.updated_at,
                    c.company, c.contact_name, c.country
             FROM crm_tasks ft
             JOIN opportunities o ON o.id = ft.opportunity_id
             LEFT JOIN customers c ON c.id = o.customer_id
             WHERE ft.reason <> ''"""
    where, args = [], []
    if opportunity_id is not None:
        where.append("ft.opportunity_id=?"); args.append(opportunity_id)
    if fu_status is not None:
        if isinstance(fu_status, (list, tuple)):
            where.append("ft.fu_status IN (%s)" % ",".join("?" * len(fu_status)))
            args += list(fu_status)
        else:
            where.append("ft.fu_status=?"); args.append(fu_status)
    if reason is not None:
        where.append("ft.reason=?"); args.append(reason)
    if active_only:
        where.append("ft.fu_status IN ('PENDING','WAITING_CUSTOMER','SNOOZED')")
    if where:
        sql += " AND " + " AND ".join(where)
    sql += " ORDER BY ft.due_at IS NULL, ft.due_at, ft.id"
    c.execute(sql, args)
    rows = c.fetchall()
    conn.close()
    keys = ("id", "opportunity_id", "title", "due_at", "owner", "status",
            "created_at", "completed_at", "reason", "fu_status",
            "next_action", "note", "stage", "product", "customer_id",
            "inquiry_id", "amount", "currency", "updated_at",
            "company", "contact", "country")
    return [dict(zip(keys, r)) for r in rows]


def mark_followup_sent(task_id, wait_days: int = 3, actor="销售"):
    """业务员确认"跟进已发送"：记 FOLLOW_UP_SENT，
    任务进入 WAITING_CUSTOMER，下次检查 = 今天 + wait_days（用户显式确认才执行）。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT ft.opportunity_id, ft.title, o.inquiry_id
                 FROM crm_tasks ft
                 LEFT JOIN opportunities o ON o.id=ft.opportunity_id
                 WHERE ft.id=?""", (task_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    due = (datetime.datetime.now()
           + datetime.timedelta(days=int(wait_days or 3))).strftime("%Y-%m-%d %H:%M")
    update_followup_task(task_id, fu_status="WAITING_CUSTOMER", due_at=due,
                         actor=actor, timeline=False)
    _fu_timeline(row[0], "FOLLOW_UP_SENT", row[1], actor,
                 metadata={"wait_days": wait_days, "due_at": due},
                 inquiry_id=row[2])
    return True


def resolve_waiting_for_deal(opportunity_id, actor="销售"):
    """客户已回复（人工确认 / 新询盘入库时）→ 该 Deal 的 WAITING_CUSTOMER
    跟进全部关闭（spec 20：不删除、不自动删；标记完成并生成新的建议）。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT id,title FROM crm_tasks
                 WHERE opportunity_id=? AND fu_status='WAITING_CUSTOMER'""",
              (opportunity_id,))
    rows = c.fetchall()
    conn.close()
    for tid, title in rows:
        update_followup_task(tid, fu_status="COMPLETED", actor=actor,
                             timeline=False)
        _fu_timeline(opportunity_id, "FOLLOW_UP_COMPLETED", title, actor,
                     metadata={"note": "客户已回复，原等待任务关闭"})
    return len(rows)


def create_opportunity(customer_id, title, product="", owner="销售", source="手动创建", details=None):
    """创建不依赖询盘的独立商机。"""
    now = _now()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO opportunities
        (customer_id,title,product,owner,source,details_json,last_activity_at,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (customer_id, title, product or None, owner, source,
         json.dumps(details or {}, ensure_ascii=False), now, now, now))
    oid = c.lastrowid
    c.execute("""INSERT INTO opportunity_stage_history
        (opportunity_id,from_stage,to_stage,changed_at,actor,reason) VALUES (?,?,?,?,?,?)""",
        (oid, None, "NEW", now, owner, "手动创建"))
    conn.commit(); conn.close()
    record_deal_activity(oid, "DEAL_CREATED", "新建商机", actor=owner)
    return oid


def record_deal_activity(opportunity_id, type_, description="", actor="销售", metadata=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT inquiry_id FROM opportunities WHERE id=?", (opportunity_id,))
    row = c.fetchone()
    if not row:
        conn.close(); return False
    now = _now()
    c.execute("""INSERT INTO deal_activity
        (opportunity_id,inquiry_id,type,ts,description,actor,metadata_json) VALUES (?,?,?,?,?,?,?)""",
        (opportunity_id, row[0], type_, now, description, actor,
         json.dumps(metadata or {}, ensure_ascii=False)))
    c.execute("UPDATE opportunities SET last_activity_at=?,updated_at=? WHERE id=?", (now, now, opportunity_id))
    conn.commit(); conn.close()
    return True


def list_deal_activity(opportunity_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT type,ts,COALESCE(description,''),actor,metadata_json
        FROM deal_activity WHERE opportunity_id=? ORDER BY ts DESC,id DESC""", (opportunity_id,))
    rows = c.fetchall()
    conn.close()
    result = []
    for type_, ts, description, actor, metadata in rows:
        try: meta = json.loads(metadata or "{}")
        except ValueError: meta = {}
        result.append({"type": type_, "ts": ts, "description": description, "actor": actor, "metadata": meta})
    return result


def mark_opportunity_won(opportunity_id, final_value, po_number="", confirmation="PO 已收到", actor="销售"):
    update_opportunity(opportunity_id, {"amount": final_value, "final_value": final_value, "po_number": po_number})
    return move_opportunity_stage(opportunity_id, "WON", actor, confirmation)


def mark_opportunity_lost(opportunity_id, reason, note="", actor="销售"):
    update_opportunity(opportunity_id, {"lost_reason": reason, "notes": note})
    return move_opportunity_stage(opportunity_id, "LOST", actor, reason)


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
