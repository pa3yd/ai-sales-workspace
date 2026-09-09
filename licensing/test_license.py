"""自检测试：验证生成/校验/过期/篡改 四种情形"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from licensing import generate_key, verify_key, SECRET

ok = True
def check(name, cond):
    global ok
    print(("  [PASS] " if cond else "  [FAIL] ") + name)
    if not cond: ok = False

print("=== 测试 1：正常 30 天卡密 ===")
k = generate_key(datetime.date.today() + datetime.timedelta(days=30), SECRET)
r = verify_key(k)
check("有效卡密通过", r["valid"] is True)
check("剩余天数约30", 28 <= r["days_left"] <= 30)

print("=== 测试 2：过期卡密（到期日设到过去）===")
expired = generate_key(datetime.date.today() - datetime.timedelta(days=1), SECRET)
r2 = verify_key(expired)
check("过期卡密被拒", r2["valid"] is False)
check("原因含'过期'", "过期" in r2["reason"])

print("=== 测试 3：篡改卡密 ===")
tampered = k[:-1] + ("A" if k[-1] != "A" else "B")
r3 = verify_key(tampered)
check("篡改卡密被拒", r3["valid"] is False)

print("=== 测试 4：完全假的卡密 ===")
r4 = verify_key("AAAA-BBBB-CCCC-DDDD-EEEE-FFFF")
check("假卡密被拒", r4["valid"] is False)

print("=== 测试 5：带横线/空格也能识别 ===")
r5 = verify_key(k.replace("-", " "))
check("去格式化后仍有效", r5["valid"] is True)

print()
print("全部通过" if ok else "存在失败项")
sys.exit(0 if ok else 1)
