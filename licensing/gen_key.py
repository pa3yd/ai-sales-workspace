"""
gen_key.py — 给你（卖家）用的发卡工具

用法：
    python gen_key.py          # 交互输入有效期天数
    python gen_key.py 30       # 直接生成 30 天卡密
    python gen_key.py 365      # 生成一年卡密
"""

import sys
import datetime
from licensing import generate_key, SECRET


def main():
    days = 30
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            print("天数必须是数字，例如：python gen_key.py 30")
            return
    expiry = datetime.date.today() + datetime.timedelta(days=days)
    key = generate_key(expiry, SECRET)
    print("=" * 44)
    print(f"有效期至 : {expiry}   (共 {days} 天)")
    print(f"卡密     : {key}")
    print("=" * 44)
    print("把上面这串卡密发给客户即可。卡密和 SECRET 绑定，别人无法伪造。")


if __name__ == "__main__":
    main()
