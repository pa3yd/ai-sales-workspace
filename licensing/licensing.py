"""
licensing.py — 轻量级卡密（License Key）模块
功能：生成带有效期的卡密、校验卡密、过期自动拒绝。
纯标准库实现，无需安装任何第三方包。

核心思路：
  卡密 = 编码后的「到期日+随机ID」 + HMAC 签名
  - 没有密钥无法伪造卡密（防随意改写到期日）
  - 每次启动检查当前日期，超过到期日就拒绝运行（自动停用）
"""

import base64
import json
import hmac
import hashlib
import datetime
import os
import sys
import threading
import time

# ===== 重要：改成你自己的随机密钥，生成端和验证端必须完全一致 =====
# 建议：随便敲一段 32 位以上的字符，且不要公开到任何网上仓库
SECRET = "TANXIN_LICENSE_SECRET_2026_please_change_me_32chars_min"

# 卡密文件：第一次输入后自动保存，之后免输入
LICENSE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".license")


# ---------------- 内部工具 ----------------
def _encode_segment(text: str) -> str:
    raw = base64.b64encode(text.encode("utf-8"))
    return raw.decode("ascii").replace("+", "-").replace("/", "_").rstrip("=")


def _decode_segment(seg: str) -> str:
    s = seg + "=" * (-len(seg) % 4)
    s = s.replace("-", "+").replace("_", "/")
    return base64.b64decode(s).decode("utf-8")


def _sign(seg: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), seg.encode("utf-8"),
                    hashlib.sha256).hexdigest()[:16]


def _format_key(raw: str) -> str:
    """每 4 位加一个短横线，方便客户抄写"""
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def _normalize_key(key: str) -> str:
    """去掉短横线和空格，恢复原始字符串"""
    return key.strip().replace("-", "").replace(" ", "")


# ---------------- 对外 API ----------------
def generate_key(expiry: datetime.date, secret: str = SECRET, uid: str = None) -> str:
    """生成一张卡密。expiry 为客户可用到的截止日期。"""
    if uid is None:
        uid = os.urandom(4).hex()
    payload = json.dumps({"exp": expiry.isoformat(), "uid": uid}, separators=(",", ":"))
    seg = _encode_segment(payload)
    sig = _sign(seg, secret)
    return _format_key(f"{seg}.{sig}")


def verify_key(key: str, secret: str = SECRET) -> dict:
    """
    校验卡密。返回 dict：
        {valid, reason, expiry, days_left, uid}
    valid=False 时 reason 说明原因（格式错误/被篡改/已过期/数据损坏）。
    """
    raw = _normalize_key(key)
    if "." not in raw:
        return {"valid": False, "reason": "卡密格式错误"}
    seg, sig = raw.rsplit(".", 1)
    # 比对签名：不一致说明卡密被改过或根本是假的
    if not hmac.compare_digest(sig, _sign(seg, secret)):
        return {"valid": False, "reason": "卡密无效或被篡改"}
    try:
        payload = json.loads(_decode_segment(seg))
        exp = datetime.date.fromisoformat(payload["exp"])
    except Exception:
        return {"valid": False, "reason": "卡密数据损坏"}
    today = datetime.date.today()
    days_left = (exp - today).days
    if today > exp:
        return {"valid": False, "reason": f"卡密已于 {exp} 过期", "expiry": exp.isoformat()}
    return {"valid": True, "expiry": exp.isoformat(),
            "days_left": days_left, "uid": payload.get("uid")}


def load_key(source_key: str = None) -> str:
    """按优先级取卡密：函数参数 > 环境变量 LICENSE_KEY > .license 文件 > 交互输入"""
    key = source_key or os.environ.get("LICENSE_KEY")
    if key:
        return key.strip()
    if os.path.exists(LICENSE_FILE):
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    key = input("请输入卡密：").strip()
    try:
        with open(LICENSE_FILE, "w", encoding="utf-8") as f:
            f.write(key)
    except Exception:
        pass
    return key


def require_license(secret: str = SECRET, key: str = None) -> dict:
    """
    在你脚本最开头调用。
    卡密无效或过期会直接退出程序（sys.exit）；通过则返回校验信息。
    """
    if secret == "TANXIN_LICENSE_SECRET_2026_please_change_me_32chars_min":
        print("[警告] 你还在使用默认 SECRET，请先在 licensing.py 中修改，否则卡密可被轻易伪造！")
    result = verify_key(load_key(key), secret)
    if not result["valid"]:
        print(f"[拒绝访问] {result['reason']}")
        print("如需购买或续期，请联系客服。")
        sys.exit(1)
    print(f"[授权成功] 有效期至 {result['expiry']}，剩余 {result.get('days_left')} 天")
    return result


def start_expiry_watchdog(secret: str = SECRET, interval_sec: int = 3600):
    """
    后台定时复查卡密（适合长时间运行的脚本）。
    到期后会自动退出程序。线程为守护线程，不阻塞主程序。
    """
    def _loop():
        while True:
            time.sleep(interval_sec)
            if not verify_key(load_key(), secret)["valid"]:
                print("[拒绝访问] 卡密已失效，程序退出。")
                os._exit(1)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
