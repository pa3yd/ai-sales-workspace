# -*- coding: utf-8 -*-
"""询盘队列 UI 展示辅助函数（无 Streamlit 依赖，便于单元测试）。"""
import datetime

# 常用国家 → 国旗 emoji（仅展示，缺省不给旗，绝不虚构）
FLAG_MAP = {
    "united kingdom": "🇬🇧", "uk": "🇬🇧", "britain": "🇬🇧", "england": "🇬🇧",
    "英国": "🇬🇧", "germany": "🇩🇪", "德国": "🇩🇪", "united states": "🇺🇸",
    "usa": "🇺🇸", "us": "🇺🇸", "美国": "🇺🇸", "france": "🇫🇷", "法国": "🇫🇷",
    "sweden": "🇸🇪", "瑞典": "🇸🇪", "australia": "🇦🇺", "澳大利亚": "🇦🇺",
    "canada": "🇨🇦", "加拿大": "🇨🇦", "netherlands": "🇳🇱", "荷兰": "🇳🇱",
    "spain": "🇪🇸", "西班牙": "🇪🇸", "italy": "🇮🇹", "意大利": "🇮🇹",
    "japan": "🇯🇵", "日本": "🇯🇵", "korea": "🇰🇷", "韩国": "🇰🇷",
    "brazil": "🇧🇷", "巴西": "🇧🇷", "mexico": "🇲🇽", "墨西哥": "🇲🇽",
    "india": "🇮🇳", "印度": "🇮🇳", "russia": "🇷🇺", "俄罗斯": "🇷🇺",
    "poland": "🇵🇱", "波兰": "🇵🇱", "norway": "🇳🇴", "挪威": "🇳🇴",
    "denmark": "🇩🇰", "丹麦": "🇩🇰", "finland": "🇫🇮", "芬兰": "🇫🇮",
    "switzerland": "🇨🇭", "瑞士": "🇨🇭", "uae": "🇦🇪", "dubai": "🇦🇪",
    "saudi": "🇸🇦", "turkey": "🇹🇷", "越南": "🇻🇳", "vietnam": "🇻🇳",
}


def flag(country) -> str:
    """国家名 → 国旗 emoji；识别不出返回空串（不硬造）。"""
    low = str(country or "").strip().lower()
    if not low:
        return ""
    for k, v in FLAG_MAP.items():
        if k in low:
            return v
    return ""


def ago(created, now=None) -> str:
    """把 created_at 转成「N分钟前」这类相对时间（仅展示用）。
    规则：刚刚 / 12分钟前 / 2小时前 / 昨天 / 3天前 / 超过7天显示「9月2日」。
    完整时间由卡片 title 悬停展示。now 参数仅供测试注入。
    """
    s = str(created or "")
    try:
        dt = datetime.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return s[:10] if s else ""
    ref = now or datetime.datetime.now()
    m = int((ref - dt).total_seconds() // 60)
    if m < 1:
        return "刚刚"
    if m < 60:
        return f"{m}分钟前"
    if m < 60 * 24:
        return f"{m // 60}小时前"
    d = m // (60 * 24)
    if d == 1:
        return "昨天"
    if d < 7:
        return f"{d}天前"
    return f"{dt.month}月{dt.day}日"


def wait(created, now=None) -> str:
    """「等待处理时长」（第六轮 UI 新增，仅展示用，业务口径不带"前"字）：
    刚刚 / 32分钟 / 5小时 / 2天 / 7天+。
    规则与 spec 十一致：不精确到秒，>7 天不再数天（业务上已属积压，
    完整时间仍由卡片 title 悬停展示）。now 参数仅供测试注入。
    """
    s = str(created or "")
    try:
        dt = datetime.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return ""
    ref = now or datetime.datetime.now()
    m = int((ref - dt).total_seconds() // 60)
    if m < 0:
        m = 0
    if m < 1:
        return "刚刚"
    if m < 60:
        return f"{m}分钟"
    h = m // 60
    if h < 24:
        return f"{h}小时"
    d = m // (60 * 24)
    if d < 7:
        return f"{d}天"
    return "7天+"


def customer_key(cust_id=None, company=None, contact=None, id_=None):
    """客户聚合身份键（第六轮 UI，仅展示聚合用，不改数据）。

    可靠性优先级：customer_id > 规范化公司名 > 联系人 > 询盘自身。
    规范化 = 去首尾 + 压缩连续空白 + 小写；绝不用模糊/AI 相似度合并。
    返回 (类型, 值) 元组，可直接当 dict 键。
    """
    if cust_id:
        try:
            return ("cid", int(cust_id))
        except Exception:
            pass
    c = " ".join(str(company or "").split()).lower()
    if c:
        return ("co", c)
    n = str(contact or "").strip().lower()
    if n:
        return ("nm", n)
    return ("id", id_)


def group_customers(items):
    """把队列条目按客户身份聚合，保持既有排序（首次出现顺序）。

    items: 带 id / company / contact / cust_id 键的字典列表（load_queue 的输出）。
    返回 [(key, [items...])]；客户只有一条询盘时组长度为 1（UI 层不显示"N个询盘"）。
    """
    ordered = {}
    for it in items:
        k = customer_key(it.get("cust_id"), it.get("company"),
                         it.get("contact"), it.get("id"))
        ordered.setdefault(k, []).append(it)
    return list(ordered.items())
