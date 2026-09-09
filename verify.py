# -*- coding: utf-8 -*-
"""
一键验收脚本：跑完下面 5 项检查，输出 PASS / FAIL 汇总表。

用法：
    python verify.py

什么时候需要跑：
  - 刚拿到项目、想确认环境没问题
  - 改了 agent/ 里的任何代码之后
  - 换了 API Key、改了 config.json 之后
  - 觉得结果不对、想快速定位是哪一环出问题
"""

import os
import sys
import json
import subprocess

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable          # 用当前这个 Python 解释器去跑子任务

results = []   # [(检查项, 是否通过, 说明)]


def run_step(name: str, args: list, success_keyword: str = None,
             fail_keywords: list = None) -> bool:
    """
    跑一条命令并判断成功与否。
    success_keyword: 输出里必须包含这个词才算通过（不填则只看返回码）
    fail_keywords  : 输出里出现这些词就算失败（比如 "[警告]"）
    """
    try:
        proc = subprocess.run(
            [PY] + args,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        results.append((name, False, "执行超时（>300秒）"))
        return False

    output = (proc.stdout or "") + (proc.stderr or "")
    detail = ""

    if fail_keywords:
        for kw in fail_keywords:
            if kw in output:
                # 抓出警告的具体内容，方便定位
                for line in output.splitlines():
                    if kw in line:
                        detail = f"输出中出现 {kw}：{line.strip()[:100]}"
                        break
                results.append((name, False, detail))
                return False

    if success_keyword and success_keyword not in output:
        results.append((name, False, f"输出中未找到预期内容「{success_keyword}」"))
        return False

    if proc.returncode != 0 and not success_keyword:
        results.append((name, False, f"返回码 {proc.returncode}"))
        return False

    results.append((name, True, "通过"))
    return True


def check_config() -> bool:
    """检查 config.json 是否填好了 Key 和真实公司信息"""
    path = os.path.join(BASE_DIR, "config.json")
    if not os.path.exists(path):
        results.append(("配置文件检查", False, "config.json 不存在"))
        return False
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    key = (cfg.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        results.append(("配置文件检查", False, "DEEPSEEK_API_KEY 还是空的"))
        return False

    company = (cfg.get("SELLER", {}).get("company") or "").strip()
    placeholders = ["xxx", "XXX", "CSTX", "Ningbo XXX", "你的"]
    if not company or any(p in company for p in placeholders):
        results.append(("配置文件检查", False,
                        f"SELLER.company 还是占位符「{company}」，客户会看到这个名字"))
        return False

    results.append(("配置文件检查", True, f"Key 已配置，公司名：{company}"))
    return True


def main():
    print("开始验收，共 5 项检查...\n")

    # 1. 配置文件
    check_config()

    # 2. API Key 连通性
    run_step("API Key 连通性", ["main.py", "--check-key"],
             success_keyword="连接成功")

    # 3. 离线自检（不花钱、不联网也能验证核心逻辑）
    run_step("离线自检", ["test_offline.py"],
             success_keyword="全部测试通过")

    # 4. LLM 全流程跑 3 条演示询盘（出现 [警告] 说明某一环降级了）
    run_step("LLM 全流程（3条演示询盘）", ["main.py"],
             success_keyword="完整分析结果已保存到",
             fail_keywords=["[警告]"])

    # 5. 自有询盘文件（存在才测）
    sample = os.path.join(BASE_DIR, "my_inquiry.txt")
    if os.path.exists(sample):
        run_step("自有询盘文件分析", ["main.py", "my_inquiry.txt"],
                 success_keyword="完整分析结果已保存到",
                 fail_keywords=["[警告]"])

    # ---------- 输出汇总表 ----------
    print("\n" + "=" * 62)
    print("验收结果汇总")
    print("=" * 62)
    passed = 0
    for name, ok, detail in results:
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name}")
        print(f"        {detail}")
        if ok:
            passed += 1
    total = len(results)
    print("-" * 62)
    print(f"  通过 {passed}/{total}")

    if passed == total:
        print("\n全部通过！项目功能正常，可以投入使用。")
        return 0
    else:
        print("\n有检查未通过，请根据上面的说明修复后重新运行。")
        print("常见问题：")
        print("  · API Key 失效或余额不足 -> 到 platform.deepseek.com 检查")
        print("  · 公司名还是占位符 -> 编辑 config.json 的 SELLER.company")
        print("  · 出现 [警告] -> 说明 LLM 某一步失败了并降级，检查网络/余额")
        return 1


if __name__ == "__main__":
    sys.exit(main())
