"""
demo_integration.py — 演示如何把卡密校验接入你自己的脚本

接入方法（三步）：
  1. 把 licensing.py 复制到你的脚本所在目录（或能 import 到的地方）
  2. 在你的脚本最开头 from licensing import require_license, SECRET
  3. 在真正干活之前调用 require_license(SECRET)

客户首次运行会提示输入卡密，之后自动记住，无需重复输入。
"""

from licensing import require_license, SECRET

# ---- 第 1 步：在最开头调用，卡密不对就直接退出 ----
require_license(SECRET)

# 如果你的脚本会长时间运行，可加这行，到期自动停用：
# from licensing import start_expiry_watchdog
# start_expiry_watchdog(SECRET, interval_sec=3600)

# ---- 第 2 步：以下是你原本的脚本逻辑 ----
print("欢迎使用本工具，卡密校验通过，开始工作……")
# your_real_script_main()
